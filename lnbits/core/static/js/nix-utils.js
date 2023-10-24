function initNix() {
  const indentSpaceCount = 2
  const typesMap = {
    attrs: 'attrs',
    float: 'number',
    bool: 'bool',
    enum: 'select',
    lines: 'text',
    package: 'str',
    path: 'str',
    port: 'number',
    str: 'str'
  }

  function handleData(data, depth = indentSpaceCount) {
    const lines = data.split('\n')
    const nextDepth = depth + indentSpaceCount

    const options = []
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].startsWith(nested('options', depth))) {
        const optionsLines = extractObject(lines.slice(i + 1), nextDepth)
        handleOptions(options, optionsLines, nextDepth)
        const optionName = lines[i].trim().split(' ')[0]
        return nestOption(optionName, {options})
      }
    }
    return options
  }

  function handleOptions(options, lines, depth) {
    const nextDepth = depth + indentSpaceCount
    for (let i = 0; i < lines.length; i++) {
      if (_lineEndsWith(lines[i], ' mkOption {')) {
        const optionLines = extractObject(lines.slice(i + 1), nextDepth)
        const optionName = lines[i].trim().split(' ')[0]
        const option = nestOption(
          optionName,
          extractOption(optionLines, nextDepth)
        )
        options.push(option)
        i += optionLines.length
      } else if (_lineEndsWith(lines[i], ' = {')) {
        const optionName = lines[i].trim().split(' ')[0]
        const option = {options: []}
        const nestedObject = extractObject(lines.slice(i + 1), nextDepth)
        handleOptions(option.options, nestedObject, nextDepth)
        if (option.options.length) {
          options.push(nestOption(optionName, option))
        }
        i += nestedObject.length
      } else if (_lineStartsWith(lines[i], 'enable = mkEnableOption')) {
        options.push({
          name: 'enable',
          type: 'bool',
          default: false,
          description: lines[i]
            .trim()
            .substring(
              'enable = mkEnableOption'.length + 1,
              lines[i].trim().length - 1
            )
        })
      }
    }
  }

  function nestOption(key, option) {
    const keys = key.split('.')
    if (keys.length === 1) {
      option.name = keys[0]
      return option
    }

    return {
      name: keys[0],
      options: [nestOption(keys.slice(1).join('.'), option)]
    }
  }

  function extractOption(lines, depth) {
    const nextDepth = depth + indentSpaceCount
    const op = {}
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim()
      if (_lineStartsWith(line, `description = mdDoc "`)) {
        op.description = line.slice(
          `description = mdDoc "`.length,
          line.length - 2
        )
      } else if (
        _lineStartsWith(line, `description = mdDoc ''`) ||
        _lineStartsWith(line, `description = ''`)
      ) {
        const longDescriptionLines = extractObject(
          lines.slice(i + 1),
          nextDepth
        )
        op.description = longDescriptionLines.map(l => l.trim()).join('\n')
        i += longDescriptionLines
      } else if (_lineStartsWith(line, 'type =') && line.endsWith(';')) {
        // todo: handle with types
        const types = line
          .substring('type ='.length + 1, line.length - 1)
          .split(' ')
          .join(';')
          .split('[')
          .join(';')
          .split(']')
          .join(';')
          .split('(')
          .join(';')
          .split(')')
          .join(';')
          .split(';')
          .filter(v => v.length)
          .map(t => (t.startsWith('types.') ? t.slice('types.'.length) : t))

        op.isList = types.includes('listOf')
        op.isOptional = types.includes('nullOr')
        const enumIndex = types.indexOf('enum')
        if (enumIndex !== -1) {
          op.values = types.slice(enumIndex + 1).map(v => extractValue(v))
        }
        if (types.find(t => t.startsWith('ints.'))) {
          op.type = 'number'
        } else {
          const type = types.find(t => typesMap[t])
          op.type = type ? typesMap[type] : 'str'
        }
      } else if (_lineStartsWith(line, 'default =') && line.endsWith(';')) {
        const value = extractValue(
          line.slice('default ='.length, line.length - 1).trim()
        )
        if (value !== undefined) {
          op.default = value
        }
      }
    }

    return op
  }
  function extractValue(value) {
    if (!Number.isNaN(+value)) {
      return +value
    } else if (value === 'true' || value === 'false') {
      return value === 'true'
    } else if (value.startsWith('"') && value.endsWith('"')) {
      return value.slice(1, value.length - 1)
    } else if (value.startsWith('[') && value.endsWith(']')) {
      return value
        .slice(1, value.length - 1)
        .split(' ')
        .filter(v => v !== '')
        .map(v => extractValue(v))
    }
  }

  function extractObject(lines, nestingLevel) {
    const prefix = nested('', nestingLevel)
    const objectLines = []
    for (const line of lines) {
      if (!line.length || line.startsWith(prefix)) {
        objectLines.push(line)
      } else {
        return objectLines
      }
    }
    return objectLines
  }

  function nested(str, level) {
    const prefix = Array(level).fill(' ').join('')
    return prefix + str
  }

  function _lineStartsWith(line, start) {
    return _normlize_line_spaces(line).startsWith(start)
  }
  function _lineEndsWith(line, end) {
    return _normlize_line_spaces(line).endsWith(end)
  }
  function _normlize_line_spaces(line = '') {
    return line
      .split(' ')
      .filter(w => w)
      .join(' ')
  }

  return handleData
}
var nixConfigToJson = initNix()

function jsonConfigToNix(options, data) {
  if (!data) return []

  const props = []
  for (const o of options) {
    if (o.options?.length > 0) {
      for (const p of jsonConfigToNix(o.options, data[o.name])) {
        props.push(`${o.name}.${p}`)
      }
    } else if (data[o.name] !== undefined && data[o.name] !== o.default) {
      props.push(`${o.name} = ${_toNixValue(o, data[o.name])}`)
    }
  }
  return props
}

function _toNixValue(option, value) {
  if (option.isList) {
    let listValues = (value || []).map(v =>
      _toNixValue({...option, isList: false}, v)
    )

    return `[${listValues.join(' ')}]`
  } else if (
    option.type === 'str' ||
    option.type === 'text' ||
    option.type === 'select'
  ) {
    if (value.indexOf('\n') !== -1) {
      return `''${value}''`
    }
    return `"${value}"`
  }

  return value
}
