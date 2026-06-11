import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
} from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { TranslateModule } from '@ngx-translate/core';
import { BreadcrumbService } from '../breadcrumb.service';
import { AnalysisActionService } from '../../features/analysis/analysis-action.service';

@Component({
  selector: 'app-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, TranslateModule],
  templateUrl: './app-header.html',
  styleUrl: './app-header.scss',
})
export class AppHeader {
  private readonly router = inject(Router);
  private readonly breadcrumbService = inject(BreadcrumbService);
  private readonly analysisActionService = inject(AnalysisActionService);

  protected readonly breadcrumbs = this.breadcrumbService.items;

  protected readonly isAnalysisPage = computed(() => {
    return this.router.url.includes('/project/');
  });

  protected restart(): void {
    this.analysisActionService.triggerRestart();
  }
}
