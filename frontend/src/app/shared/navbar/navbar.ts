import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  inject,
  model,
  OnInit,
  output,
  signal,
} from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import { TranslateModule } from '@ngx-translate/core';
import { Repo, RepoStore } from '../../core/api';
import { UserService } from '../../core/auth';
import { AppLanguage, LanguageService } from '../../core/i18n';
import { AnalyzeDialog } from '../../features/dashboard/analyze-dialog/analyze-dialog';
import { version } from '../../../../package.json';

const GRAPH_PHOTO_URL = 'https://graph.microsoft.com/v1.0/me/photo/$value';

const ICON_COLORS: Record<string, string> = {
  typescript: '#6366F1',
  python: '#F97316',
  java: '#EF4444',
  cobol: '#10B981',
};

const DEFAULT_ICON_COLOR = '#F59E0B';

@Component({
  selector: 'app-navbar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, TranslateModule, AnalyzeDialog],
  templateUrl: './navbar.html',
  styleUrl: './navbar.scss',
})
export class Navbar implements OnInit {
  private readonly document = inject(DOCUMENT);
  private readonly router = inject(Router);
  private readonly http = inject(HttpClient);
  private readonly destroyRef = inject(DestroyRef);
  private readonly repoStore = inject(RepoStore);
  protected readonly userService = inject(UserService);
  protected readonly languageService = inject(LanguageService);

  readonly collapsed = model(false);
  readonly analyzeCreated = output<string>();

  protected readonly appVersion = version;
  protected readonly repos = toSignal(this.repoStore.getRepos(), { initialValue: [] });

  protected readonly isAnalyzeOpen = signal(false);
  protected readonly isProfileMenuOpen = signal(false);
  private previousFocus: HTMLElement | null = null;

  protected readonly photoUrl = signal<string | null>(null);
  protected readonly currentLang = this.languageService.currentLang;

  protected readonly initials = computed(() => {
    const name = this.userService.user()?.name ?? '';
    const parts = name.split(' ').filter(Boolean);
    if (parts.length === 0) return '?';
    if (parts.length === 1) return parts[0][0].toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  });

  protected readonly userName = computed(() => this.userService.user()?.name ?? '');

  ngOnInit(): void {
    this.loadPhoto();
  }

  protected toggleCollapsed(): void {
    this.collapsed.update((v) => !v);
  }

  protected folderColor(repo: Repo): string {
    if (repo.color) {
      return repo.color;
    }
    return ICON_COLORS[repo.languages[0]] ?? DEFAULT_ICON_COLOR;
  }

  protected setLanguage(lang: AppLanguage): void {
    this.languageService.setLanguage(lang);
  }

  protected openAnalyzeDialog(): void {
    this.previousFocus = document.activeElement as HTMLElement;
    this.isAnalyzeOpen.set(true);
  }

  protected closeAnalyzeDialog(): void {
    this.isAnalyzeOpen.set(false);
    setTimeout(() => this.previousFocus?.focus());
  }

  protected onProjectCreated(repoId: string): void {
    this.isAnalyzeOpen.set(false);
    this.analyzeCreated.emit(repoId);
    this.router.navigate(['/project', repoId]);
  }

  protected navigateToProject(repoId: string): void {
    this.router.navigate(['/project', repoId]);
  }

  protected toggleProfileMenu(): void {
    this.isProfileMenuOpen.update((v) => !v);
  }

  protected closeProfileMenu(): void {
    this.isProfileMenuOpen.set(false);
  }

  protected logout(): void {
    this.document.defaultView?.location.assign('.auth/logout');
  }

  protected refreshSession(): void {
    this.userService.clearAzureCookies();
    this.document.defaultView?.location.reload();
  }

  private loadPhoto(): void {
    this.http
      .get(GRAPH_PHOTO_URL, { responseType: 'blob' })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (blob) => {
          this.photoUrl.set(URL.createObjectURL(blob));
        },
        error: () => {
          this.photoUrl.set(null);
        },
      });
  }
}
