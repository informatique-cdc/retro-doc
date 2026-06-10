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

@Pipe({ name: 'markdown' })
export class MarkdownPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) {
      return '';
    }
    return marked.parse(value, { async: false }) as string;
  }
}
