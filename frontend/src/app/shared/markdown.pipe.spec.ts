import { MarkdownPipe, MarkdownStreamPipe } from './markdown.pipe';

describe('MarkdownStreamPipe', () => {
  const pipe = new MarkdownStreamPipe();

  it('renders markdown from a partial answer', () => {
    expect(pipe.transform('## Title')).toContain('<h2');
    expect(pipe.transform('- one\n- two')).toContain('<li>');
    expect(pipe.transform('**bold**')).toContain('<strong>bold</strong>');
  });

  it('closes a code fence that has not been closed yet', () => {
    // The closing fence has not streamed in; the block still renders as code.
    const html = pipe.transform('```python\nprint("hi")');
    expect(html).toContain('<pre>');
    expect(html).toContain('language-python');
  });

  it('keeps a mermaid block as code so the diagram is only drawn once complete', () => {
    const source = '```mermaid\ngraph TD;\n  A-->B;\n```';
    expect(pipe.transform(source)).not.toContain('class="mermaid"');
    expect(pipe.transform(source)).toContain('<pre>');
    // The finished renderer is the one that hands the source to mermaid.
    expect(new MarkdownPipe().transform(source)).toContain('class="mermaid"');
  });

  it('escapes an unhighlighted block instead of emitting it as markup', () => {
    const html = pipe.transform('```\n<img src=x onerror=alert(1)>\n```');
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img');
  });

  it('renders nothing for an answer that has not started', () => {
    expect(pipe.transform('')).toBe('');
    expect(pipe.transform(null)).toBe('');
    expect(pipe.transform(undefined)).toBe('');
  });

  it('grows the rendered output as tokens arrive', () => {
    const tokens = ['Here', ' is ', '`code`', ' and a\n\n- ', 'list item'];
    let text = '';
    const renders = tokens.map((token) => {
      text += token;
      return pipe.transform(text);
    });

    expect(renders[0]).toContain('Here');
    expect(renders[2]).toContain('<code>code</code>');
    expect(renders[4]).toContain('<li>list item</li>');
  });
});
