import { DOCUMENT } from '@angular/common';
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
import { BehaviorSubject, combineLatest, map, switchMap, takeWhile, timer } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { PipelineStatus, Repo, RepoService, RepoStore } from '../../core/api';
import { LanguageService } from '../../core/i18n';
import { BreadcrumbService } from '../../shared/breadcrumb.service';
import { MarkdownPipe } from '../../shared/markdown.pipe';
import { MermaidDirective } from '../../shared/mermaid.directive';
import { timeAgo } from '../../shared/time-ago';
import { EditRepoDialog } from '../dashboard/edit-repo-dialog/edit-repo-dialog';
import { UiButton, UiSpinner } from '@design-system';
import { EXTENSION_META, OTHER_COLOR } from './language-meta';

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

function isPipelineActive(status: PipelineStatus): boolean {
  return status === 'pending' || status === 'running';
}

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

  protected readonly pipelineStatus = toSignal(
    this.repoId$.pipe(
      switchMap((id) =>
        this.repoService.getPipelineStatus(id).pipe(
          switchMap((initial) => {
            if (!isPipelineActive(initial.status)) {
              return [initial];
            }
            return timer(0, 10_000).pipe(
              switchMap(() => this.repoService.getPipelineStatus(id)),
              takeWhile((res) => isPipelineActive(res.status), true)
            );
          })
        )
      )
    )
  );

  private previousPipelineStatus: PipelineStatus | undefined;

  protected readonly isRunning = computed(() => this.pipelineStatus()?.status === 'running');

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

  protected readonly isZipUpload = computed(() => !this.repo()?.repo_branch);

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

  protected startAnalysis(): void {
    if (this.isRunning()) {
      return;
    }
    const repo = this.repo();
    if (repo) {
      this.router.navigate(['/project', repo.repo_id, 'analysis']);
    }
  }
}
