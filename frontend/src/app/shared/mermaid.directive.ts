import { AfterViewInit, Directive, ElementRef, inject, input, OnChanges } from '@angular/core';
import mermaid from 'mermaid';

let mermaidInitialized = false;
let renderCounter = 0;

@Directive({ selector: '[appMermaid]' })
export class MermaidDirective implements AfterViewInit, OnChanges {
  private readonly el = inject(ElementRef<HTMLElement>);

  /** Bind to [innerHTML] value so the directive re-runs when content changes. */
  appMermaid = input<string>();

  ngAfterViewInit(): void {
    this.renderMermaid();
  }

  ngOnChanges(): void {
    this.renderMermaid();
  }

  private async renderMermaid(): Promise<void> {
    if (!mermaidInitialized) {
      mermaid.initialize({ startOnLoad: false, theme: 'default' });
      mermaidInitialized = true;
    }

    const container: HTMLElement = this.el.nativeElement;
    const mermaidDivs = container.querySelectorAll('div.mermaid');

    for (const node of mermaidDivs) {
      const div = node as HTMLElement;
      if (div.dataset['processed']) continue;
      div.dataset['processed'] = 'true';

      const source = div.textContent?.trim();
      if (!source) continue;

      try {
        const id = `mermaid-${++renderCounter}`;
        const { svg } = await mermaid.render(id, source);

        // Wrap the chart in a container with a download button
        const wrapper = document.createElement('div');
        wrapper.className = 'mermaid-wrapper';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'mermaid-download-btn';
        btn.setAttribute('aria-label', 'Download chart as PNG');
        btn.innerHTML =
          '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">' +
          '<path d="M8 2v8M8 10L5 7M8 10l3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>' +
          '<path d="M2 12v1.5A1.5 1.5 0 0 0 3.5 15h9a1.5 1.5 0 0 0 1.5-1.5V12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>' +
          '</svg>' +
          '<span>PNG</span>';

        btn.addEventListener('click', () => this.downloadAsPng(wrapper));

        wrapper.appendChild(btn);

        // Move the rendered SVG into the wrapper
        div.innerHTML = '';
        const svgContainer = document.createElement('div');
        svgContainer.className = 'mermaid';
        svgContainer.innerHTML = svg;
        wrapper.appendChild(svgContainer);

        div.replaceWith(wrapper);
      } catch {
        div.classList.add('mermaid--error');
      }
    }
  }

  private downloadAsPng(wrapper: HTMLElement): void {
    // Target the mermaid chart SVG, not the button icon SVG
    const svgEl = wrapper.querySelector('.mermaid svg') as SVGSVGElement | null;
    if (!svgEl) return;

    const clone = svgEl.cloneNode(true) as SVGSVGElement;

    // Compute dimensions from the rendered element
    const bbox = svgEl.getBoundingClientRect();
    const scale = 2; // 2x for retina clarity
    const width = Math.ceil(bbox.width * scale);
    const height = Math.ceil(bbox.height * scale);

    // Inline computed styles from the document into the SVG so they
    // survive serialization (mermaid uses <style> blocks that may
    // reference classes; inlining ensures they render in the <img>).
    const styleSheets = document.querySelectorAll('style');
    const styleEl = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    let cssText = '';
    styleSheets.forEach((sheet) => {
      if (sheet.textContent?.includes('.mermaid') || sheet.textContent?.includes('#mermaid')) {
        cssText += sheet.textContent + '\n';
      }
    });
    if (cssText) {
      styleEl.textContent = cssText;
      clone.insertBefore(styleEl, clone.firstChild);
    }

    clone.setAttribute('width', `${width}`);
    clone.setAttribute('height', `${height}`);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');

    // Remove the mermaid accessibility title to keep the export clean
    clone.removeAttribute('role');
    clone.removeAttribute('aria-roledescription');

    const serializer = new XMLSerializer();
    const svgString = serializer.serializeToString(clone);

    // Use a data URL (base64) instead of a Blob URL for reliable
    // cross-browser rendering inside <img>
    const base64 = btoa(unescape(encodeURIComponent(svgString)));
    const dataUrl = `data:image/svg+xml;base64,${base64}`;

    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;

      const ctx = canvas.getContext('2d')!;
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(img, 0, 0, width, height);

      canvas.toBlob((blob) => {
        if (!blob) return;
        const pngUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = pngUrl;
        link.download = 'chart.png';
        link.click();
        URL.revokeObjectURL(pngUrl);
      }, 'image/png');
    };
    img.src = dataUrl;
  }
}
