import { DOCUMENT } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import {
  BehaviorSubject,
  catchError,
  combineLatest,
  filter,
  map,
  Observable,
  of,
  switchMap,
  takeWhile,
  timer,
} from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import {
  PipelineStatus,
  PipelineStatusResponse,
  RelaunchRepoResponse,
  Repo,
  RepoService,
  RepoStore,
} from '../../core/api';
import { LanguageService } from '../../core/i18n';
import { BreadcrumbService } from '../../shared/breadcrumb.service';
import { MarkdownPipe } from '../../shared/markdown.pipe';
import { MermaidDirective } from '../../shared/mermaid.directive';
import { timeAgo } from '../../shared/time-ago';
import { EditRepoDialog } from '../dashboard/edit-repo-dialog/edit-repo-dialog';
import { UiButton, UiSpinner } from '@design-system';
import { EXTENSION_META, OTHER_COLOR } from './language-meta';
import { RelaunchDialog, RelaunchReason } from './relaunch-dialog/relaunch-dialog';

const LANGUAGE_LABELS: Record<string, string> = {
  java: 'Java',
  python: 'Python',
  typescript: 'TypeScript',
  cobol: 'COBOL',
};

export interface LanguageBreakdown {
  name: string;
  percentage: number;
  color: string;
}

/** One earlier analysis attempt, resolved for display. */
export interface AttemptView {
  status: PipelineStatus;
  when: string;
  /** Superseded by a later retry, rather than a failure that still stands. */
  superseded: boolean;
  step: string | null;
}

function isPipelineActive(status: PipelineStatus): boolean {
  return status === 'pending' || status === 'running';
}

/**
 * A repository the backend holds no run for.
 *
 * Its own state rather than a failure: the backend answers 404 there, and that
 * is reachable — a create that died before inserting its run is repaired by a
 * relaunch, nothing sweeps for it — so it is worth telling apart from a status
 * we simply could not read.
 */
const NO_PIPELINE_RUN = 'none';

/** A status the backend would not give us, which says nothing about the run. */
const PIPELINE_UNKNOWN = 'unknown';

type PipelineState =
  | PipelineStatusResponse
  | typeof NO_PIPELINE_RUN
  | typeof PIPELINE_UNKNOWN;

// Greyed-out filler shown behind the "coming soon" badge when a repo has no
// language stats yet (e.g. a freshly uploaded repo still being analyzed).
const PLACEHOLDER_LANGUAGES: LanguageBreakdown[] = [
  { name: 'TypeScript', percentage: 65, color: '#3178c6' },
  { name: 'JavaScript', percentage: 20, color: '#f1e05a' },
  { name: 'CSS', percentage: 10, color: '#563d7c' },
  { name: 'HTML', percentage: 5, color: '#e34c26' },
];

@Component({
  selector: 'app-project',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    MarkdownPipe,
    MermaidDirective,
    TranslateModule,
    EditRepoDialog,
    RelaunchDialog,
    UiButton,
    UiSpinner,
  ],
  templateUrl: './project.html',
  styleUrl: './project.scss',
})
export class Project {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly repoService = inject(RepoService);
  private readonly repoStore = inject(RepoStore);
  private readonly breadcrumbService = inject(BreadcrumbService);
  private readonly translateService = inject(TranslateService);
  private readonly languageService = inject(LanguageService);
  private readonly document = inject(DOCUMENT);

  private readonly reload$ = new BehaviorSubject<void>(undefined);
  private readonly repoId$ = this.route.paramMap.pipe(map((params) => params.get('id')!));

  protected readonly repoId = toSignal(this.repoId$);

  protected readonly repo = toSignal(
    combineLatest([this.repoId$, this.reload$]).pipe(
      switchMap(([id]) => this.repoStore.getRepo(id))
    )
  );

  protected readonly files = toSignal(
    combineLatest([this.repoId$, this.reload$]).pipe(
      switchMap(([id]) => this.repoStore.getRepoFiles(id))
    ),
    { initialValue: [] }
  );

  // Nexted after a relaunch, so polling restarts on the run it just dispatched
  // instead of sitting on the terminal status it had settled at
  private readonly pipelineReload$ = new BehaviorSubject<void>(undefined);

  private readonly pipelineState = toSignal(
    combineLatest([this.repoId$, this.pipelineReload$]).pipe(
      switchMap(([id]) => this.watchPipeline(id))
    ),
    { initialValue: PIPELINE_UNKNOWN as PipelineState }
  );

  /** The latest pipeline read, or null when there is no run to describe. */
  protected readonly pipelineStatus = computed(() => {
    const state = this.pipelineState();
    return typeof state === 'string' ? null : state;
  });

  /**
   * The backend holds no run for this repository, so there is nothing to
   * report and a relaunch is what dispatches one.
   */
  protected readonly noPipelineRun = computed(
    () => this.pipelineState() === NO_PIPELINE_RUN
  );

  private previousPipelineStatus: PipelineStatus | undefined;

  // PENDING counts as analyzing: a relaunch lands there first, and treating it
  // as settled would show a freshly dispatched run as a finished one
  protected readonly isAnalyzing = computed(() => {
    const status = this.pipelineStatus()?.status;
    return status !== undefined && isPipelineActive(status);
  });

  protected readonly analysisFailed = computed(
    () => this.pipelineStatus()?.status === 'failed'
  );

  /** The latest attempt's failing step and message, when it left one. */
  protected readonly failureMeta = computed(() => this.pipelineStatus()?.meta ?? null);

  /**
   * The attempts before the current one, newest first.
   *
   * `attempts[0]` is dropped: it is the run the headline status and meta
   * already describe, so listing it again would only repeat it.
   */
  protected readonly previousAttempts = computed<AttemptView[]>(() => {
    this.languageService.currentLang();
    return (this.pipelineStatus()?.attempts ?? []).slice(1).map((attempt) => ({
      status: attempt.status,
      when: timeAgo(attempt.finished_at ?? attempt.started_at, this.translateService),
      superseded: attempt.retried_at !== null,
      step: attempt.meta?.step ?? null,
    }));
  });

  /** The analyzer the documentation was produced by, once a run has stamped one. */
  protected readonly analyzerVersion = computed(() => this.repo()?.analyzer_version ?? null);

  /**
   * Whether a newer analyzer exists for this repository, offered only once
   * nothing is in flight — the backend refuses a relaunch over a live run, and
   * a failed one is offered as a retry instead.
   */
  protected readonly canRelaunch = computed(
    () => this.repo()?.stale === true && !this.isAnalyzing() && !this.analysisFailed()
  );

  protected readonly repoName = computed(() => this.repo()?.name ?? '');

  protected readonly repoDescription = computed(() => {
    this.languageService.currentLang();
    const repo = this.repo();
    if (!repo) {
      return '';
    }
    const languages = repo.languages ?? [];
    if (languages.length === 0) {
      return this.translateService.instant('project.repositoryGeneric');
    }
    const labels = languages.map((code) => LANGUAGE_LABELS[code] ?? code).join(', ');
    return this.translateService.instant('project.repository', { lang: labels });
  });

  protected readonly fileCount = computed(() => this.files().length);

  protected readonly lastEdit = computed(() => {
    this.languageService.currentLang();
    const repo = this.repo();
    return repo ? timeAgo(repo.updated_at, this.translateService) : '';
  });

  protected readonly linkCopied = signal(false);
  protected readonly editingRepo = signal<Repo | null>(null);
  protected readonly relaunchReason = signal<RelaunchReason | null>(null);

  // A zip upload is the repository with no commit pinned to it — the same test
  // the backend uses to tell its two sources apart
  protected readonly isZipUpload = computed(() => !this.repo()?.repo_hash);

  protected readonly branchCount = signal(12);
  protected readonly contributorCount = signal(8);

  protected readonly languages = computed<LanguageBreakdown[]>(() => {
    this.languageService.currentLang();
    const otherLabel = this.translateService.instant('project.otherLanguages');
    const byExtension = this.repo()?.stats?.files_by_extension;
    if (!byExtension) {
      return [];
    }

    const totals = new Map<string, { count: number; color: string }>();
    let total = 0;
    for (const [rawExt, rawCount] of Object.entries(byExtension)) {
      const count = rawCount ?? 0;
      if (count <= 0) {
        continue;
      }
      total += count;
      const ext = rawExt.replace(/^\.+/, '').toLowerCase();
      const meta = EXTENSION_META[ext] ?? { name: otherLabel, color: OTHER_COLOR };
      const existing = totals.get(meta.name);
      if (existing) {
        existing.count += count;
      } else {
        totals.set(meta.name, { count, color: meta.color });
      }
    }

    if (total === 0) {
      return [];
    }

    return [...totals.entries()]
      .map(([name, { count, color }]) => ({
        name,
        color,
        percentage: Math.round((count / total) * 100),
      }))
      .filter((lang) => lang.percentage > 0)
      .sort((a, b) => b.percentage - a.percentage);
  });

  protected readonly hasLanguageData = computed(() => this.languages().length > 0);

  protected readonly displayLanguages = computed(() =>
    this.hasLanguageData() ? this.languages() : PLACEHOLDER_LANGUAGES
  );

  constructor() {
    effect(() => {
      const name = this.repoName();
      if (name) {
        this.breadcrumbService.set([
          { label: 'common.dashboard', route: '/' },
          { label: name },
        ]);
      } else {
        this.breadcrumbService.set([
          { label: 'common.dashboard', route: '/' },
          { label: 'common.loading' },
        ]);
      }
    });

    // When the analysis pipeline transitions from active to a terminal state,
    // the repo now has fresh stats and files. Re-fetch so the UI updates without
    // a manual page reload.
    effect(() => {
      const id = this.repoId();
      const status = this.pipelineStatus()?.status;
      const previous = this.previousPipelineStatus;
      this.previousPipelineStatus = status;

      if (!id || !status) {
        return;
      }

      const wasActive = previous !== undefined && isPipelineActive(previous);
      const finished = !isPipelineActive(status);
      if (wasActive && finished) {
        this.repoStore.invalidateRepo(id);
        this.reload$.next();
      }
    });
  }

  /**
   * Follow one repository's pipeline, polling only while a run is live.
   *
   * The poll skips a read it could not make rather than publishing it: the last
   * status read is still the better answer, and the next tick either confirms
   * or replaces it. Stopping on one would strand the page on a blip.
   */
  private watchPipeline(id: string): Observable<PipelineState> {
    return this.readPipeline(id).pipe(
      switchMap((initial) => {
        if (typeof initial === 'string' || !isPipelineActive(initial.status)) {
          return of(initial);
        }
        return timer(0, 10_000).pipe(
          switchMap(() => this.readPipeline(id)),
          filter((state) => state !== PIPELINE_UNKNOWN),
          takeWhile(
            (state) => typeof state !== 'string' && isPipelineActive(state.status),
            true
          )
        );
      })
    );
  }

  /**
   * One pipeline read, resolved to a state rather than left as an error.
   *
   * An error would end the stream for good, taking the polling and the reload
   * that a relaunch depends on with it, so the page could never recover from
   * one. A 404 is not even a failure: it is how the backend says nothing has
   * run for this repository yet.
   */
  private readPipeline(id: string): Observable<PipelineState> {
    return this.repoService.getPipelineStatus(id).pipe(
      catchError((err: HttpErrorResponse) =>
        of<PipelineState>(err.status === 404 ? NO_PIPELINE_RUN : PIPELINE_UNKNOWN)
      )
    );
  }

  protected async shareProject(): Promise<void> {
    const repo = this.repo();
    if (!repo) return;

    const origin = this.document.location.origin;
    const url = `${origin}/import?repo=${encodeURIComponent(repo.repo_id)}`;

    try {
      await navigator.clipboard.writeText(url);
      this.linkCopied.set(true);
      setTimeout(() => this.linkCopied.set(false), 2000);
    } catch {
      // Clipboard API may fail in insecure contexts
    }
  }

  protected openEditDialog(): void {
    const repo = this.repo();
    if (repo) {
      this.editingRepo.set(repo);
    }
  }

  protected closeEditDialog(): void {
    this.editingRepo.set(null);
  }

  protected onRepoSaved(): void {
    this.editingRepo.set(null);
    const repo = this.repo();
    if (repo) {
      this.repoStore.invalidateRepo(repo.repo_id);
    }
  }

  protected onRepoDeleted(): void {
    this.editingRepo.set(null);
    this.router.navigate(['/']);
  }

  protected openRelaunchDialog(reason: RelaunchReason): void {
    this.relaunchReason.set(reason);
  }

  protected closeRelaunchDialog(): void {
    this.relaunchReason.set(null);
  }

  /**
   * Follow a relaunch to wherever its analysis ended up.
   *
   * A new `repo_id` is the backend having moved this caller onto an analysis at
   * a newer analyzer version, which lives elsewhere and is worth navigating to;
   * this repository stays listed either way. The same id is a failed run
   * retried in place, so there is nowhere to go — only a new run to follow.
   */
  protected onRelaunched(result: RelaunchRepoResponse): void {
    this.relaunchReason.set(null);

    const current = this.repoId();
    if (current === undefined || result.repo_id !== current) {
      this.router.navigate(['/project', result.repo_id]);
      return;
    }

    this.repoStore.invalidateRepo(current);
    this.reload$.next();
    this.pipelineReload$.next();
  }

  protected startAnalysis(): void {
    if (this.isAnalyzing()) {
      return;
    }
    const repo = this.repo();
    if (repo) {
      this.router.navigate(['/project', repo.repo_id, 'analysis']);
    }
  }
}
