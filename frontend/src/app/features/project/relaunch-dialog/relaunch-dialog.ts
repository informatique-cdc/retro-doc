import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  ElementRef,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { finalize } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { UiButton, UiInput } from '@design-system';
import { RelaunchRepoResponse, Repo, RepoService, RepoStore } from '../../../core/api';

/**
 * Why the dialog was opened.
 *
 * Only wording depends on it: the request is identical in every case, and
 * whether the backend retries the failed run, settles a wedged one, dispatches
 * the run a create never inserted, or moves the caller to a newer analyzer
 * version is a fact about the repository rather than about the ask.
 */
export type RelaunchReason = 'failed' | 'never' | 'stale' | 'stuck';

interface ReasonCopy {
  title: string;
  explanation: string;
  confirm: string;
  pending: string;
  /**
   * Whether to promise that nothing is replaced.
   *
   * True everywhere there is something to keep. A repository nothing has run
   * for has no documentation, graphs, threads or deep analyses, so the promise
   * is about nothing and only reads as a warning.
   */
  keepsExisting: boolean;
}

const REASON_COPY: Record<RelaunchReason, ReasonCopy> = {
  failed: {
    title: 'relaunchDialog.retryTitle',
    explanation: 'relaunchDialog.explainFailed',
    confirm: 'relaunchDialog.retryConfirm',
    pending: 'relaunchDialog.relaunching',
    keepsExisting: true,
  },
  never: {
    title: 'relaunchDialog.startTitle',
    explanation: 'relaunchDialog.explainNever',
    confirm: 'relaunchDialog.startConfirm',
    pending: 'relaunchDialog.starting',
    keepsExisting: false,
  },
  stale: {
    title: 'relaunchDialog.title',
    explanation: 'relaunchDialog.explainStale',
    confirm: 'relaunchDialog.confirm',
    pending: 'relaunchDialog.relaunching',
    keepsExisting: true,
  },
  stuck: {
    title: 'relaunchDialog.retryTitle',
    explanation: 'relaunchDialog.explainStuck',
    confirm: 'relaunchDialog.retryConfirm',
    pending: 'relaunchDialog.relaunching',
    keepsExisting: true,
  },
};

@Component({
  selector: 'app-relaunch-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule, UiButton, UiInput],
  templateUrl: './relaunch-dialog.html',
  styleUrl: './relaunch-dialog.scss',
})
export class RelaunchDialog implements AfterViewInit {
  readonly repo = input.required<Repo>();
  readonly reason = input.required<RelaunchReason>();
  readonly closed = output<void>();
  readonly relaunched = output<RelaunchRepoResponse>();

  private readonly repoService = inject(RepoService);
  private readonly repoStore = inject(RepoStore);
  private readonly destroyRef = inject(DestroyRef);
  private readonly translateService = inject(TranslateService);

  private readonly dialogRef = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  protected readonly token = signal('');
  protected readonly loading = signal(false);
  protected readonly serverError = signal<string | null>(null);

  /**
   * Only a git source can ask for a credential: an archive is re-read from the
   * upload that already identifies it, with no remote to reach.
   */
  protected readonly isGit = computed(() => this.repo().repo_hash !== null);

  private readonly copy = computed(() => REASON_COPY[this.reason()]);

  protected readonly titleKey = computed(() => this.copy().title);

  protected readonly explanationKey = computed(() => this.copy().explanation);

  protected readonly confirmKey = computed(() => this.copy().confirm);

  protected readonly pendingKey = computed(() => this.copy().pending);

  protected readonly showKeepsExisting = computed(() => this.copy().keepsExisting);

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

  protected onTokenInput(value: string): void {
    this.token.set(value);
  }

  protected onConfirm(): void {
    this.serverError.set(null);
    this.loading.set(true);

    this.repoService
      .relaunchRepo(this.repo().repo_id, this.token().trim() || undefined)
      .pipe(
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (response) => {
          // A relaunch onto a newer version adds a repository to the caller's
          // list, so the list is stale here whether or not we navigate to it
          this.repoStore.invalidateRepos();
          this.close();
          this.relaunched.emit(response);
        },
        error: (err: HttpErrorResponse) => {
          this.serverError.set(this.errorMessage(err));
        },
      });
  }

  /**
   * Turn a refused relaunch into something the user can act on.
   *
   * 409 is the ordinary "nothing to do" — already current, already in flight,
   * or already relaunched — and is worth telling apart from a remote that
   * rejected the token it was given, or one that did not answer at all.
   */
  private errorMessage(err: HttpErrorResponse): string {
    switch (err.status) {
      case 409:
        return this.translateService.instant('relaunchDialog.nothingToDo');
      case 422:
        return this.translateService.instant('relaunchDialog.unresolved');
      case 502:
        return this.translateService.instant('relaunchDialog.unreachable');
      default:
        return this.translateService.instant('relaunchDialog.failed');
    }
  }
}
