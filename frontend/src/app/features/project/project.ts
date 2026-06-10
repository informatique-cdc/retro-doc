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
import { switchMap, takeWhile, timer } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Repo, RepoService, RepoStore } from '../../core/api';
import { LanguageService } from '../../core/i18n';
import { BreadcrumbService } from '../../shared/breadcrumb.service';
import { MarkdownPipe } from '../../shared/markdown.pipe';
import { MermaidDirective } from '../../shared/mermaid.directive';
import { timeAgo } from '../../shared/time-ago';
import { EditRepoDialog } from '../dashboard/edit-repo-dialog/edit-repo-dialog';
import { UiButton, UiSpinner } from '@design-system';

export interface LanguageBreakdown {
  name: string;
  percentage: number;
  color: string;
}

const MOCK_LANGUAGES: LanguageBreakdown[] = [
  { name: 'TypeScript', percentage: 65, color: '#3b82f6' },
  { name: 'JavaScript', percentage: 20, color: '#eab308' },
  { name: 'CSS', percentage: 10, color: '#ec4899' },
  { name: 'HTML', percentage: 5, color: '#f97316' },
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

  protected readonly repo = toSignal(
    this.route.paramMap.pipe(
      switchMap((params) => this.repoStore.getRepo(params.get('id')!))
    )
  );

  protected readonly files = toSignal(
    this.route.paramMap.pipe(
      switchMap((params) => this.repoStore.getRepoFiles(params.get('id')!))
    ),
    { initialValue: [] }
  );

  protected readonly pipelineStatus = toSignal(
    this.route.paramMap.pipe(
      switchMap((params) => {
        const id = params.get('id')!;
        return this.repoService.getPipelineStatus(id).pipe(
          switchMap((initial) => {
            if (initial.status !== 'running') {
              return [initial];
            }
            return timer(0, 10_000).pipe(
              switchMap(() => this.repoService.getPipelineStatus(id)),
              takeWhile((res) => res.status === 'running', true)
            );
          })
        );
      })
    )
  );

  protected readonly isRunning = computed(() => this.pipelineStatus()?.status === 'running');

  protected readonly repoName = computed(() => this.repo()?.name ?? '');

  protected readonly repoDescription = computed(() => {
    const repo = this.repo();
    if (!repo) {
      return '';
    }
    return this.translateService.instant('project.repository', { lang: repo.language });
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

  protected readonly languages = signal<LanguageBreakdown[]>(MOCK_LANGUAGES);

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
