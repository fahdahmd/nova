/**
 * A very small, safe Markdown renderer.
 *
 * Model output is never trusted: this builds real DOM nodes and only ever sets
 * text through `textContent`, so there is no HTML-injection surface at all
 * (and nothing here needs `innerHTML`, which the page's CSP forbids anyway).
 *
 * Supported: paragraphs, headings, unordered/ordered lists, blockquotes,
 * fenced and inline code, bold, italic, links (http/https only) and line
 * breaks. Anything else is rendered as plain text.
 */

const INLINE_PATTERN =
  /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)|(\[[^\]]+\]\([^)\s]+\))/g;

const SAFE_LINK = /^https?:\/\//i;

/**
 * @param {string} text
 * @returns {DocumentFragment}
 */
export function renderMarkdown(text) {
  const fragment = document.createDocumentFragment();
  const lines = String(text ?? '').replace(/\r\n/g, '\n').split('\n');

  let index = 0;
  while (index < lines.length) {
    const line = lines[index];

    // Fenced code block
    if (/^\s*```/.test(line)) {
      const body = [];
      index += 1;
      while (index < lines.length && !/^\s*```/.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1;
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      code.textContent = body.join('\n');
      pre.append(code);
      fragment.append(pre);
      continue;
    }

    // Blank line
    if (!line.trim()) {
      index += 1;
      continue;
    }

    // Heading
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = Math.min(6, heading[1].length + 2);
      const element = document.createElement(`h${level}`);
      appendInline(element, heading[2]);
      fragment.append(element);
      index += 1;
      continue;
    }

    // Blockquote
    if (/^\s*>\s?/.test(line)) {
      const quote = document.createElement('blockquote');
      const body = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        body.push(lines[index].replace(/^\s*>\s?/, ''));
        index += 1;
      }
      appendInline(quote, body.join(' '));
      fragment.append(quote);
      continue;
    }

    // Lists
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      const list = document.createElement(ordered ? 'ol' : 'ul');
      while (index < lines.length) {
        const current = lines[index];
        const match = ordered
          ? /^\s*\d+[.)]\s+(.*)$/.exec(current)
          : /^\s*[-*+]\s+(.*)$/.exec(current);
        if (!match) break;
        const item = document.createElement('li');
        appendInline(item, match[1]);
        list.append(item);
        index += 1;
      }
      fragment.append(list);
      continue;
    }

    // Paragraph: gather until a blank line or a new block starts.
    const paragraph = document.createElement('p');
    const parts = [];
    while (index < lines.length) {
      const current = lines[index];
      if (
        !current.trim() ||
        /^\s*```/.test(current) ||
        /^(#{1,4})\s+/.test(current) ||
        /^\s*>\s?/.test(current) ||
        /^\s*[-*+]\s+/.test(current) ||
        /^\s*\d+[.)]\s+/.test(current)
      ) {
        break;
      }
      parts.push(current);
      index += 1;
    }
    parts.forEach((part, position) => {
      if (position > 0) paragraph.append(document.createElement('br'));
      appendInline(paragraph, part);
    });
    fragment.append(paragraph);
  }

  return fragment;
}

/**
 * Append inline-formatted text to an element, as DOM nodes only.
 * @param {HTMLElement} target
 * @param {string} text
 */
function appendInline(target, text) {
  const source = String(text ?? '');
  let cursor = 0;

  for (const match of source.matchAll(INLINE_PATTERN)) {
    const token = match[0];
    const start = match.index ?? 0;
    if (start > cursor) target.append(source.slice(cursor, start));

    if (token.startsWith('`')) {
      const code = document.createElement('code');
      code.textContent = token.slice(1, -1);
      target.append(code);
    } else if (token.startsWith('**') || token.startsWith('__')) {
      const strong = document.createElement('strong');
      strong.textContent = token.slice(2, -2);
      target.append(strong);
    } else if (token.startsWith('[')) {
      const divider = token.indexOf('](');
      const label = token.slice(1, divider);
      const href = token.slice(divider + 2, -1);
      if (SAFE_LINK.test(href)) {
        const anchor = document.createElement('a');
        anchor.href = href;
        anchor.textContent = label;
        anchor.rel = 'noopener noreferrer nofollow';
        anchor.target = '_blank';
        target.append(anchor);
      } else {
        // Anything that is not plain http(s) is shown, never linked.
        target.append(`${label} (${href})`);
      }
    } else {
      const em = document.createElement('em');
      em.textContent = token.slice(1, -1);
      target.append(em);
    }

    cursor = start + token.length;
  }

  if (cursor < source.length) target.append(source.slice(cursor));
}
