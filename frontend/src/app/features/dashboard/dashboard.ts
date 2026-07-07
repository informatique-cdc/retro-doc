import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { combineLatest, debounceTime, distinctUntilChanged, map, switchMap, tap } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Repo, RepoService, RepoStore } from '../../core/api';
import { BreadcrumbService } from '../../shared/breadcrumb.service';
import { timeAgo } from '../../shared/time-ago';
import { EditRepoDialog } from './edit-repo-dialog/edit-repo-dialog';

const LANGUAGE_LABELS: Record<string, string> = {
  java: 'Java',
  python: 'Python',
  typescript: 'TypeScript',
  cobol: 'COBOL',
};

const ICON_COLORS: Record<string, { fill: string; bg: string }> = {
  typescript: { fill: '#6366F1', bg: '#EEF2FF' },
  python: { fill: '#F97316', bg: '#FFF7ED' },
  java: { fill: '#EF4444', bg: '#FEF2F2' },
  cobol: { fill: '#10B981', bg: '#ECFDF5' },
};

const DEFAULT_ICON_COLORS = { fill: '#6B7280', bg: '#F3F4F6' };

function hexToTint(hex: string): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const tint = (c: number) =>
    Math.round(c + (255 - c) * 0.85)
      .toString(16)
      .padStart(2, '0');
  return `#${tint(r)}${tint(g)}${tint(b)}`;
}

@Component({
  selector: 'app-dashboard',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule, FormsModule, EditRepoDialog],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard {
  private readonly repoService = inject(RepoService);
  private readonly repoStore = inject(RepoStore);
  private readonly router = inject(Router);
  private readonly breadcrumbService = inject(BreadcrumbService);
  private readonly translateService = inject(TranslateService);

  protected readonly searchQuery = signal('');
  protected readonly skeletonItems = Array.from({ length: 8 });

  private readonly searchQuery$ = toObservable(this.searchQuery).pipe(
    debounceTime(300),
    distinctUntilChanged()
  );

  protected readonly searching = signal(false);

  protected readonly filteredRepos = toSignal(
    combineLatest([this.searchQuery$, this.repoStore.reposInvalidated$]).pipe(
      map(([query]) => query),
      tap(() => this.searching.set(true)),
      switchMap((query) => {
        const trimmed = query.trim();
        return this.repoService.getRepos(trimmed || undefined);
      }),
      tap(() => this.searching.set(false))
    )
  );

  protected readonly loading = computed(() => this.filteredRepos() === undefined);
  protected readonly editingRepo = signal<Repo | null>(null);

  constructor() {
    this.breadcrumbService.set([{ label: 'common.dashboard' }]);
  }

  protected languageLabel(repo: Repo): string {
    return repo.languages.map((code) => LANGUAGE_LABELS[code] ?? code).join(', ');
  }

  protected timeAgo(isoDate: string): string {
    return timeAgo(isoDate, this.translateService);
  }

  protected iconColors(repo: Repo): { fill: string; bg: string } {
    if (repo.color) {
      return { fill: repo.color, bg: hexToTint(repo.color) };
    }
    return ICON_COLORS[repo.languages[0]] ?? DEFAULT_ICON_COLORS;
  }

  protected openEditDialog(repo: Repo, event: Event): void {
    event.stopPropagation();
    this.editingRepo.set(repo);
  }

  protected closeEditDialog(): void {
    this.editingRepo.set(null);
  }

  protected onRepoSaved(): void {
    this.editingRepo.set(null);
  }

  protected onRepoDeleted(): void {
    this.editingRepo.set(null);
  }

  protected navigateToProject(repo: Repo): void {
    this.router.navigate(['/project', repo.repo_id]);
  }
}
