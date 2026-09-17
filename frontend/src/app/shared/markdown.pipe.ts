import { Pipe, PipeTransform } from '@angular/core';
import { Marked } from 'marked';
import { markedHighlight } from 'marked-highlight';
import hljs from 'highlight.js';

const marked = new Marked(
  markedHighlight({
    emptyLangClass: 'hljs',
    langPrefix: 'hljs language-',
    highlight(code, lang) {
      if (lang === 'mermaid') {
        return code;
      }
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    },
  }),
  {
    renderer: {
      code({ text, lang }) {
        if (lang === 'mermaid') {
          return `<div class="mermaid">${text}</div>`;
        }
        const langClass = lang ? `hljs language-${lang}` : 'hljs';
        return `<pre><code class="${langClass}">${text}</code></pre>`;
      },
    },
  }
);

/**
 * Above this size a block is left unhighlighted while it streams.
 *
 * Highlighting re-runs over the whole block for every token that extends it,
 * so a long listing gets more expensive with each token it grows by. The
 * finished render highlights it once, in full.
 */
const STREAMING_HIGHLIGHT_LIMIT = 5000;

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Renderer for an answer that is still arriving.
 *
 * It drops the two things the finished renderer does that cannot be repeated
 * on every token: `hljs.highlightAuto`, which guesses a language by trying
 * every grammar it knows, and the mermaid container, whose diagram can only be
 * drawn from a complete source. An untagged or mermaid block therefore reads
 * as plain code until the stream ends and `markdown` takes over.
 */
const streamingMarked = new Marked({
  renderer: {
    code({ text, lang }) {
      // marked passes the whole info string; only its first word names a language.
      const language = /\S*/.exec(lang ?? '')?.[0] ?? '';
      const highlightable =
        language !== 'mermaid' &&
        text.length <= STREAMING_HIGHLIGHT_LIMIT &&
        Boolean(hljs.getLanguage(language));
      const body = highlightable ? hljs.highlight(text, { language }).value : escapeHtml(text);
      const langClass = language ? `hljs language-${language}` : 'hljs';
      return `<pre><code class="${langClass}">${body}</code></pre>`;
    },
  },
});

@Pipe({ name: 'markdown' })
export class MarkdownPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) {
      return '';
    }
    return marked.parse(value, { async: false }) as string;
  }
}

/**
 * Renders the part of an answer that has arrived so far, so markdown takes
 * shape as it streams instead of appearing at once when the stream ends.
 *
 * Half-written markdown is unbalanced by nature — an open fence, an unclosed
 * emphasis, a table with no separator row yet. marked closes those constructs
 * at the end of its input, so every intermediate render is valid HTML and the
 * text settles into place as the rest of the tokens arrive.
 */
@Pipe({ name: 'markdownStream' })
export class MarkdownStreamPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) {
      return '';
    }
    return streamingMarked.parse(value, { async: false }) as string;
  }
}
