/**
 * A deliberately small Markdown renderer.
 *
 * The agent's answers use a known, narrow subset — headings, bold, italics,
 * inline code, bullet lists, blockquotes and pipe tables — so a focused ~120
 * line renderer beats pulling a library off a CDN that may not be reachable.
 *
 * Everything is HTML-escaped before any markup is produced, so API text (and
 * anything a model wrote) can never inject nodes into the page.
 */

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** Inline spans, applied to already-escaped text. */
function inline(text) {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[\s(])_([^_\n]+)_/g, '$1<em>$2</em>');
}

/** `|---|---:|` → per-column alignment. */
function alignments(row) {
  return splitRow(row).map((cell) => {
    const c = cell.trim();
    if (c.endsWith(':') && c.startsWith(':')) return 'center';
    if (c.endsWith(':')) return 'right';
    return 'left';
  });
}

function splitRow(row) {
  return row.trim().replace(/^\||\|$/g, '').split('|');
}

function isDivider(line) {
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
}

function isTableRow(line) {
  return line.trim().startsWith('|') && line.trim().endsWith('|');
}

export function renderMarkdown(source) {
  const lines = escapeHtml(source).replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) { i += 1; continue; }

    // Table: a header row, a divider, then body rows.
    if (isTableRow(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const align = alignments(lines[i + 1]);
      const header = splitRow(line);
      const cell = (text, idx, tag) => {
        const style = align[idx] && align[idx] !== 'left' ? ` class="num"` : '';
        return `<${tag}${style}>${inline(text.trim())}</${tag}>`;
      };
      const body = [];
      i += 2;
      while (i < lines.length && isTableRow(lines[i])) {
        body.push(`<tr>${splitRow(lines[i]).map((c, n) => cell(c, n, 'td')).join('')}</tr>`);
        i += 1;
      }
      out.push(
        '<div class="table-wrap"><table><thead><tr>' +
        header.map((c, n) => cell(c, n, 'th')).join('') +
        `</tr></thead><tbody>${body.join('')}</tbody></table></div>`
      );
      continue;
    }

    // Heading.
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 1, 6); // never emit an <h1>
      out.push(`<h${level}>${inline(heading[2].trim())}</h${level}>`);
      i += 1;
      continue;
    }

    // Blockquote (used for the unmet-demand disclaimer).
    if (/^&gt;\s?/.test(line)) {
      const quoted = [];
      while (i < lines.length && /^&gt;\s?/.test(lines[i])) {
        quoted.push(lines[i].replace(/^&gt;\s?/, ''));
        i += 1;
      }
      out.push(`<blockquote>${inline(quoted.join(' '))}</blockquote>`);
      continue;
    }

    // Lists.
    const bullet = /^\s*[-*+]\s+/;
    const ordered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || ordered.test(line)) {
      const isOrdered = ordered.test(line);
      const pattern = isOrdered ? ordered : bullet;
      const tag = isOrdered ? 'ol' : 'ul';
      const items = [];
      while (i < lines.length && pattern.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(pattern, '').trim())}</li>`);
        i += 1;
      }
      out.push(`<${tag}>${items.join('')}</${tag}>`);
      continue;
    }

    // Paragraph: consume until a blank line or the start of another block.
    const paragraph = [];
    while (
      i < lines.length && lines[i].trim() &&
      !isTableRow(lines[i]) && !/^(#{1,6})\s/.test(lines[i]) &&
      !/^&gt;\s?/.test(lines[i]) && !bullet.test(lines[i]) && !ordered.test(lines[i])
    ) {
      paragraph.push(lines[i].trim());
      i += 1;
    }
    if (paragraph.length) out.push(`<p>${inline(paragraph.join(' '))}</p>`);
  }

  return out.join('\n');
}
