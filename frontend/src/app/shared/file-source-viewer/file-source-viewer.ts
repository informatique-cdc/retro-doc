import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import hljs from 'highlight.js';
import { finalize, Observable } from 'rxjs';
import { RepoService } from '../../core/api';
import { MarkdownPipe } from '../markdown.pipe';
import { MermaidDirective } from '../mermaid.directive';

export type FileContentMode = 'source' | 'documentation';

const MIN_WIDTH_PERCENT = 25;
const MAX_WIDTH_PERCENT = 90;
const DEFAULT_WIDTH_PERCENT = 40;
const KEYBOARD_STEP_PERCENT = 5;

@Component({
  selector: 'app-file-source-viewer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule, MarkdownPipe, MermaidDirective],
  templateUrl: './file-source-viewer.html',
  styleUrl: './file-source-viewer.scss',
})
export class FileSourceViewer implements AfterViewInit {
  readonly repoId = input.required<string>();
  readonly fileId = input.required<string>();
  readonly mode = input<FileContentMode>('source');
  readonly closed = output<void>();

  private readonly repoService = inject(RepoService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly translateService = inject(TranslateService);
  private readonly document = inject(DOCUMENT);

  private readonly dialogRef = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  protected readonly content = signal<string | null>(null);
  protected readonly path = signal<string | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly widthPercent = signal(DEFAULT_WIDTH_PERCENT);
  protected readonly resizing = signal(false);
  protected readonly minWidthPercent = MIN_WIDTH_PERCENT;
  protected readonly maxWidthPercent = MAX_WIDTH_PERCENT;

  private resizeStartX = 0;
  private resizeStartPercent = DEFAULT_WIDTH_PERCENT;

  protected readonly highlightedSource = computed(() => {
    const content = this.content();
    if (content === null || this.mode() === 'documentation') return '';
    const ext = this.path()?.split('.').pop()?.toLowerCase();
    if (ext && hljs.getLanguage(ext)) {
      return hljs.highlight(content, { language: ext }).value;
    }
    return hljs.highlightAuto(content).value;
  });

  constructor() {
    effect(() => this.fetchContent(this.repoId(), this.fileId(), this.mode()));
  }

  ngAfterViewInit(): void {
    this.dialogRef().nativeElement.showModal();
  }

  protected close(): void {
    this.dialogRef().nativeElement.close();
  }

  protected onDialogClose(): void {
    this.closed.emit();
  }

  protected onBackdropClick(event: MouseEvent): void {
    if (event.target === this.dialogRef().nativeElement) {
      this.close();
    }
  }

  protected onEscape(event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    this.close();
  }

  protected onResizeStart(event: PointerEvent): void {
    (event.target as HTMLElement).setPointerCapture(event.pointerId);
    this.resizing.set(true);
    this.resizeStartX = event.clientX;
    this.resizeStartPercent = this.widthPercent();
  }

  protected onResizeMove(event: PointerEvent): void {
    if (!this.resizing()) return;
    const viewportWidth = this.document.defaultView?.innerWidth ?? 0;
    if (viewportWidth === 0) return;
    const deltaPercent = ((this.resizeStartX - event.clientX) / viewportWidth) * 100;
    this.setWidthPercent(this.resizeStartPercent + deltaPercent);
  }

  protected onResizeEnd(event: PointerEvent): void {
    this.resizing.set(false);
    (event.target as HTMLElement).releasePointerCapture(event.pointerId);
  }

  protected onResizeKeydown(event: KeyboardEvent): void {
    if (event.key === 'ArrowLeft') {
      this.setWidthPercent(this.widthPercent() + KEYBOARD_STEP_PERCENT);
      event.preventDefault();
    } else if (event.key === 'ArrowRight') {
      this.setWidthPercent(this.widthPercent() - KEYBOARD_STEP_PERCENT);
      event.preventDefault();
    }
  }

  private setWidthPercent(percent: number): void {
    this.widthPercent.set(Math.min(MAX_WIDTH_PERCENT, Math.max(MIN_WIDTH_PERCENT, percent)));
  }

  private fetchContent(repoId: string, fileId: string, mode: FileContentMode): void {
    this.loading.set(true);
    this.error.set(null);
    this.content.set(null);
    this.path.set(null);

    const request: Observable<{ content: string; path?: string }> =
      mode === 'documentation'
        ? this.repoService.getFileDoc(repoId, fileId)
        : this.repoService.getFileSource(repoId, fileId);
    const errorKey =
      mode === 'documentation' ? 'fileSourceViewer.docLoadFailed' : 'fileSourceViewer.loadFailed';

    request
      .pipe(
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (data) => {
          this.content.set(data.content);
          this.path.set(data.path ?? null);
        },
        error: () => {
          this.error.set(this.translateService.instant(errorKey));
        },
      });
  }
}
