import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { TranslateModule } from '@ngx-translate/core';
import { RepoService } from '../../core/api';
import { UiButton, UiSpinner } from '@design-system';

type ImportState = 'loading' | 'error';

@Component({
  selector: 'app-import-project',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule, UiButton, UiSpinner],
  templateUrl: './import-project.html',
  styleUrl: './import-project.scss',
})
export class ImportProject implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly repoService = inject(RepoService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly state = signal<ImportState>('loading');

  ngOnInit(): void {
    const repoId = this.route.snapshot.queryParamMap.get('repo');

    if (!repoId) {
      this.state.set('error');
      return;
    }

    this.repoService
      .importRepo(repoId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.router.navigate(['/project', response.repo_id]);
        },
        error: (err: HttpErrorResponse) => {
          if (err.status === 409) {
            this.router.navigate(['/project', repoId]);
          } else {
            this.state.set('error');
          }
        },
      });
  }

  protected goToDashboard(): void {
    this.router.navigate(['/']);
  }
}
