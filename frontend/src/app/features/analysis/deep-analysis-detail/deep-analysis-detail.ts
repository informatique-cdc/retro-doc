import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
} from '@angular/core';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { DeepAnalysisDetail as DeepAnalysisDetailModel } from '../../../core/api';
import { MarkdownPipe } from '../../../shared/markdown.pipe';
import { UiButton, UiSpinner } from '@design-system';

const STATUS_TRANSLATION_KEYS: Record<string, string> = {
  pending: 'analysis.deepAnalysisStatusPending',
  running: 'analysis.deepAnalysisStatusRunning',
  completed: 'analysis.deepAnalysisStatusCompleted',
  failed: 'analysis.deepAnalysisStatusFailed',
};

@Component({
  selector: 'app-deep-analysis-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule, MarkdownPipe, UiButton, UiSpinner],
  templateUrl: './deep-analysis-detail.html',
  styleUrl: './deep-analysis-detail.scss',
})
export class DeepAnalysisDetailComponent {
  readonly analysis = input<DeepAnalysisDetailModel | null>(null);
  readonly downloadPdf = output<string>();
  readonly retry = output<string>();

  private readonly translateService = inject(TranslateService);

  protected readonly statusLabel = computed(() => {
    const detail = this.analysis();
    if (!detail) return '';
    const key = STATUS_TRANSLATION_KEYS[detail.status] ?? detail.status;
    return this.translateService.instant(key);
  });

  protected onDownloadPdf(): void {
    const detail = this.analysis();
    if (detail) {
      this.downloadPdf.emit(detail.id);
    }
  }

  protected onRetry(): void {
    const detail = this.analysis();
    if (detail) {
      this.retry.emit(detail.id);
    }
  }
}
