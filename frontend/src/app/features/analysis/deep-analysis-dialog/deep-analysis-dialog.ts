import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { finalize } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { DeepAnalysisService } from '../../../core/api';
import { UiButton } from '@design-system';

@Component({
  selector: 'app-deep-analysis-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule, UiButton],
  templateUrl: './deep-analysis-dialog.html',
  styleUrl: './deep-analysis-dialog.scss',
})
export class DeepAnalysisDialog implements AfterViewInit {
  readonly repoId = input.required<string>();
  readonly closed = output<void>();
  readonly started = output<string>();

  private readonly deepAnalysisService = inject(DeepAnalysisService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly translateService = inject(TranslateService);

  private readonly dialogRef = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  protected readonly query = signal('');
  protected readonly loading = signal(false);
  protected readonly serverError = signal<string | null>(null);

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

  protected onQueryInput(event: Event): void {
    this.query.set((event.target as HTMLTextAreaElement).value);
  }

  protected onSubmit(): void {
    const q = this.query().trim();
    if (!q || this.loading()) return;

    this.loading.set(true);
    this.serverError.set(null);

    this.deepAnalysisService
      .createAnalysis(this.repoId(), q)
      .pipe(
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (analysis) => {
          this.close();
          this.started.emit(analysis.id);
        },
        error: () => {
          this.serverError.set(
            this.translateService.instant('analysis.deepAnalysisError')
          );
        },
      });
  }
}
