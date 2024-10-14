from http import HTTPStatus

from bolt11 import decode as bolt11_decode
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from loguru import logger

from lnbits.core.extensions.extension_manager import (
    activate_extension,
    deactivate_extension,
    install_extension,
    uninstall_extension,
)
from lnbits.core.extensions.models import (
    CreateExtension,
    Extension,
    ExtensionConfig,
    ExtensionMeta,
    ExtensionRelease,
    InstallableExtension,
    PayToEnableInfo,
    ReleasePaymentInfo,
    UserExtension,
    UserExtensionInfo,
)
from lnbits.core.models import (
    SimpleStatus,
    User,
)
from lnbits.core.services import check_transaction_status, create_invoice
from lnbits.decorators import (
    check_admin,
    check_user_exists,
)

from ..crud import (
    create_user_extension,
    delete_dbversion,
    drop_extension_db,
    get_db_version,
    get_installed_extension,
    get_installed_extensions,
    get_user_extension,
    update_installed_extension,
    update_user_extension,
)

extension_router = APIRouter(
    tags=["Extension Managment"],
    prefix="/api/v1/extension",
)


@extension_router.post("", dependencies=[Depends(check_admin)])
async def api_install_extension(data: CreateExtension):
    release = await InstallableExtension.get_extension_release(
        data.ext_id, data.source_repo, data.archive, data.version
    )
    if not release:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Release not found"
        )

    if not release.is_version_compatible:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Incompatible extension version"
        )

    release.payment_hash = data.payment_hash
    ext_meta = ExtensionMeta(installed_release=release)
    ext_info = InstallableExtension(
        id=data.ext_id,
        name=data.ext_id,
        version=data.version,
        meta=ext_meta,
        icon=release.icon,
    )

    try:
        extension = await install_extension(ext_info)

    except Exception as exc:
        logger.warning(exc)
        ext_info.clean_extension_files()
        detail = (
            str(exc)
            if isinstance(exc, AssertionError)
            else f"Failed to install extension '{ext_info.id}'."
            f"({ext_info.installed_version})."
        )
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=detail,
        ) from exc

    try:
        await activate_extension(extension)
        return extension
    except Exception as exc:
        logger.warning(exc)
        await deactivate_extension(extension.code)
        detail = (
            str(exc)
            if isinstance(exc, AssertionError)
            else f"Extension `{extension.code}` installed, but activation failed."
        )
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=detail,
        ) from exc


@extension_router.get("/{ext_id}/details")
async def api_extension_details(
    ext_id: str,
    details_link: str,
):
    all_releases = await InstallableExtension.get_extension_releases(ext_id)

    release = next((r for r in all_releases if r.details_link == details_link), None)
    if not release:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Release not found"
        )

    release_details = await ExtensionRelease.fetch_release_details(details_link)
    if not release_details:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Cannot fetch details for release",
        )
    release_details["icon"] = release.icon
    release_details["repo"] = release.repo
    return release_details


@extension_router.put("/{ext_id}/sell")
async def api_update_pay_to_enable(
    ext_id: str,
    data: PayToEnableInfo,
    user: User = Depends(check_admin),
) -> SimpleStatus:
    if data.wallet not in user.wallet_ids:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, "Wallet does not belong to this admin user."
        )
    extension = await get_installed_extension(ext_id)
    if not extension:
        raise HTTPException(HTTPStatus.NOT_FOUND, f"Extension '{ext_id}' not found.")
    if extension.meta:
        extension.meta.pay_to_enable = data
    else:
        extension.meta = ExtensionMeta(pay_to_enable=data)
    await update_installed_extension(extension)
    return SimpleStatus(
        success=True, message=f"Payment info updated for '{ext_id}' extension."
    )


@extension_router.put("/{ext_id}/enable")
async def api_enable_extension(
    ext_id: str, user: User = Depends(check_user_exists)
) -> SimpleStatus:
    if ext_id not in [e.code for e in Extension.get_valid_extensions()]:
        raise HTTPException(
            HTTPStatus.NOT_FOUND, f"Extension '{ext_id}' doesn't exist."
        )
    try:
        logger.info(f"Enabling extension: {ext_id}.")
        ext = await get_installed_extension(ext_id)
        assert ext, f"Extension '{ext_id}' is not installed."
        assert ext.active, f"Extension '{ext_id}' is not activated."

        user_ext = await get_user_extension(user.id, ext_id)
        if not user_ext:
            user_ext = UserExtension(user=user.id, extension=ext_id, active=False)
            await create_user_extension(user_ext)

        if user.admin or not ext.requires_payment:
            user_ext.active = True
            await update_user_extension(user_ext)
            return SimpleStatus(success=True, message=f"Extension '{ext_id}' enabled.")

        if not (user_ext.extra and user_ext.extra.payment_hash_to_enable):
            raise HTTPException(
                HTTPStatus.PAYMENT_REQUIRED, f"Extension '{ext_id}' requires payment."
            )

        if user_ext.is_paid:
            user_ext.active = True
            await update_user_extension(user_ext)
            return SimpleStatus(
                success=True, message=f"Paid extension '{ext_id}' enabled."
            )

        assert (
            ext.meta and ext.meta.pay_to_enable and ext.meta.pay_to_enable.wallet
        ), f"Extension '{ext_id}' is missing payment wallet."

        payment_status = await check_transaction_status(
            wallet_id=ext.meta.pay_to_enable.wallet,
            payment_hash=user_ext.extra.payment_hash_to_enable,
        )

        if not payment_status.paid:
            raise HTTPException(
                HTTPStatus.PAYMENT_REQUIRED,
                f"Invoice generated but not paid for enabeling extension '{ext_id}'.",
            )

        user_ext.active = True
        user_ext.extra.paid_to_enable = True
        await update_user_extension(user_ext)
        return SimpleStatus(success=True, message=f"Paid extension '{ext_id}' enabled.")

    except AssertionError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, str(exc)) from exc
    except HTTPException as exc:
        raise exc from exc
    except Exception as exc:
        logger.warning(exc)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=(f"Failed to enable '{ext_id}' "),
        ) from exc


@extension_router.put("/{ext_id}/disable")
async def api_disable_extension(
    ext_id: str, user: User = Depends(check_user_exists)
) -> SimpleStatus:
    if ext_id not in [e.code for e in Extension.get_valid_extensions()]:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, f"Extension '{ext_id}' doesn't exist."
        )
    user_ext = await get_user_extension(user.id, ext_id)
    if not user_ext or not user_ext.active:
        return SimpleStatus(
            success=True, message=f"Extension '{ext_id}' already disabled."
        )
    logger.info(f"Disabeling extension: {ext_id}.")
    user_ext.active = False
    await update_user_extension(user_ext)
    return SimpleStatus(success=True, message=f"Extension '{ext_id}' disabled.")


@extension_router.put("/{ext_id}/activate", dependencies=[Depends(check_admin)])
async def api_activate_extension(ext_id: str) -> SimpleStatus:
    try:
        logger.info(f"Activating extension: '{ext_id}'.")

        ext = Extension.get_valid_extension(ext_id)
        assert ext, f"Extension '{ext_id}' doesn't exist."

        await activate_extension(ext)
        return SimpleStatus(success=True, message=f"Extension '{ext_id}' activated.")
    except Exception as exc:
        logger.warning(exc)
        await deactivate_extension(ext_id)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=(f"Failed to activate '{ext_id}'."),
        ) from exc


@extension_router.put("/{ext_id}/deactivate", dependencies=[Depends(check_admin)])
async def api_deactivate_extension(ext_id: str) -> SimpleStatus:
    try:
        logger.info(f"Deactivating extension: '{ext_id}'.")

        ext = Extension.get_valid_extension(ext_id)
        assert ext, f"Extension '{ext_id}' doesn't exist."

        await deactivate_extension(ext_id)
        return SimpleStatus(success=True, message=f"Extension '{ext_id}' deactivated.")
    except Exception as exc:
        logger.warning(exc)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=(f"Failed to deactivate '{ext_id}'."),
        ) from exc


@extension_router.delete("/{ext_id}", dependencies=[Depends(check_admin)])
async def api_uninstall_extension(ext_id: str) -> SimpleStatus:

    extension = await get_installed_extension(ext_id)
    if not extension:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail=f"Unknown extension id: {ext_id}",
        )

    installed_extensions = await get_installed_extensions()
    # check that other extensions do not depend on this one
    for valid_ext_id in [ext.code for ext in Extension.get_valid_extensions()]:
        installed_ext = next(
            (ext for ext in installed_extensions if ext.id == valid_ext_id), None
        )
        if (
            installed_ext
            and installed_ext.meta
            and ext_id in installed_ext.meta.dependencies
        ):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=(
                    f"Cannot uninstall. Extension '{installed_ext.name}' "
                    "depends on this one."
                ),
            )

    try:
        await uninstall_extension(ext_id)

        logger.success(f"Extension '{ext_id}' uninstalled.")
        return SimpleStatus(success=True, message=f"Extension '{ext_id}' uninstalled.")
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


@extension_router.get("/{ext_id}/releases", dependencies=[Depends(check_admin)])
async def get_extension_releases(ext_id: str) -> list[ExtensionRelease]:
    try:
        extension_releases: list[ExtensionRelease] = (
            await InstallableExtension.get_extension_releases(ext_id)
        )

        installed_ext = await get_installed_extension(ext_id)
        if not installed_ext:
            return extension_releases

        for release in extension_releases:
            payment_info = installed_ext.find_existing_payment(release.pay_link)
            if payment_info:
                release.paid_sats = payment_info.amount

        return extension_releases

    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


@extension_router.put("/{ext_id}/invoice/install", dependencies=[Depends(check_admin)])
async def get_pay_to_install_invoice(
    ext_id: str, data: CreateExtension
) -> ReleasePaymentInfo:
    try:
        assert (
            ext_id == data.ext_id
        ), f"Wrong extension id. Expected {ext_id}, but got {data.ext_id}"
        assert data.cost_sats, "A non-zero amount must be specified."
        release = await InstallableExtension.get_extension_release(
            data.ext_id, data.source_repo, data.archive, data.version
        )
        assert release, "Release not found."
        assert release.pay_link, "Pay link not found for release."

        payment_info = await release.fetch_release_payment_info(data.cost_sats)

        assert payment_info and payment_info.payment_request, "Cannot request invoice."
        invoice = bolt11_decode(payment_info.payment_request)

        assert invoice.amount_msat is not None, "Invoic amount is missing."
        invoice_amount = int(invoice.amount_msat / 1000)
        assert (
            invoice_amount == data.cost_sats
        ), f"Wrong invoice amount: {invoice_amount}."
        assert (
            payment_info.payment_hash == invoice.payment_hash
        ), "Wrong invoice payment hash."

        return payment_info

    except AssertionError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        logger.warning(exc)
        raise HTTPException(
            HTTPStatus.INTERNAL_SERVER_ERROR, "Cannot request invoice"
        ) from exc


@extension_router.put("/{ext_id}/invoice/enable")
async def get_pay_to_enable_invoice(
    ext_id: str, data: PayToEnableInfo, user: User = Depends(check_user_exists)
):
    if not data.amount or data.amount <= 0:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Amount must be greater than 0."
        )

    ext = await get_installed_extension(ext_id)
    if not ext:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail=f"Extension '{ext_id}' not found."
        )

    if not ext.meta or not ext.meta.pay_to_enable:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Payment info not found for extension '{ext_id}'.",
        )

    if not ext.meta.pay_to_enable.required:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Payment not required for extension '{ext_id}'.",
        )

    if not ext.meta.pay_to_enable.wallet or not ext.meta.pay_to_enable.amount:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Payment wallet or amount missing for extension '{ext_id}'.",
        )

    if data.amount < ext.meta.pay_to_enable.amount:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=(
                f"Amount {data.amount} sats is less than required "
                f"{ext.meta.pay_to_enable.amount} sats."
            ),
        )

    payment_hash, payment_request = await create_invoice(
        wallet_id=ext.meta.pay_to_enable.wallet,
        amount=data.amount,
        memo=f"Enable '{ext.name}' extension.",
    )

    user_ext = await get_user_extension(user.id, ext_id)
    if not user_ext:
        user_ext = UserExtension(user=user.id, extension=ext_id, active=False)
        await create_user_extension(user_ext)
    user_ext_info = user_ext.extra if user_ext.extra else UserExtensionInfo()
    user_ext_info.payment_hash_to_enable = payment_hash
    user_ext.extra = user_ext_info
    await update_user_extension(user_ext)
    return {"payment_hash": payment_hash, "payment_request": payment_request}


@extension_router.get(
    "/release/{org}/{repo}/{tag_name}",
    dependencies=[Depends(check_admin)],
)
async def get_extension_release(org: str, repo: str, tag_name: str):
    try:
        config = await ExtensionConfig.fetch_github_release_config(org, repo, tag_name)
        if not config:
            return {}

        return {
            "min_lnbits_version": config.min_lnbits_version,
            "is_version_compatible": config.is_version_compatible(),
            "warning": config.warning,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


@extension_router.delete(
    "/{ext_id}/db",
    dependencies=[Depends(check_admin)],
)
async def delete_extension_db(ext_id: str):
    try:
        db_version = await get_db_version(ext_id)
        if not db_version:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Unknown extension id: {ext_id}",
            )
        await drop_extension_db(ext_id=ext_id)
        await delete_dbversion(ext_id=ext_id)
        logger.success(f"Database removed for extension '{ext_id}'")
        return SimpleStatus(
            success=True, message=f"DB deleted for '{ext_id}' extension."
        )
    except HTTPException as ex:
        logger.error(ex)
        raise ex
    except Exception as exc:
        logger.error(exc)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Cannot delete data for extension '{ext_id}'",
        ) from exc
