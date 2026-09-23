import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  ElementRef,
  inject,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { finalize, Observable } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { UiButton, UiDropzone, UiInput } from '@design-system';
import { AnalyzeFileResponse, RepoService, RepoStore } from '../../../core/api';

type UploadMethod = 'git' | 'zip';

interface DropzoneFile {
  id: string;
  name: string;
  size: number;
  status: 'uploading' | 'uploaded' | 'error' | 'failed';
  progress?: number;
  error?: string;
}

interface ColorOption {
  value: string;
  label: string;
}

const LANGUAGE_LABELS: Record<string, string> = {
  java: 'Java',
  python: 'Python',
  typescript: 'TypeScript',
  cobol: 'COBOL',
};

/** Mirrors the backend's commit check, so a typo is caught before a round trip. */
const COMMIT_PATTERN = /^[0-9a-fA-F]{7,64}$/;

/**
 * Whether a string is an http(s) URL, the only shape the backend accepts.
 *
 * `URL` rejects a missing host for these schemes, so the authority the git
 * remote is fetched from is covered too.
 */
function isHttpUrl(value: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  return parsed.protocol === 'http:' || parsed.protocol === 'https:';
}

const PROJECT_COLORS: ColorOption[] = [
  { value: '#3B82F6', label: 'Blue' },
  { value: '#F59E0B', label: 'Orange' },
  { value: '#EF4444', label: 'Red' },
  { value: '#991B1B', label: 'Maroon' },
  { value: '#7C3AED', label: 'Purple' },
  { value: '#22C55E', label: 'Green' },
  { value: '#86EFAC', label: 'Light green' },
  { value: '#F3F4F6', label: 'Light gray' },
  { value: '#9CA3AF', label: 'Gray' },
  { value: '#4B5563', label: 'Dark gray' },
  { value: '#111827', label: 'Black' },
];

@Component({
  selector: 'app-analyze-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule, UiButton, UiDropzone, UiInput],
  templateUrl: './analyze-dialog.html',
  styleUrl: './analyze-dialog.scss',
})
export class AnalyzeDialog implements AfterViewInit {
  readonly closed = output<void>();
  readonly created = output<string>();

  private readonly repoService = inject(RepoService);
  private readonly repoStore = inject(RepoStore);
  private readonly destroyRef = inject(DestroyRef);
  private readonly translateService = inject(TranslateService);

  private readonly dialogRef = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  protected readonly uploadMethod = signal<UploadMethod>('zip');
  protected readonly projectName = signal('');
  protected readonly autoDetect = signal(true);
  protected readonly selectedLanguages = signal<string[]>([]);
  protected readonly availableLanguages = toSignal(this.repoService.getSupportedLanguages(), {
    initialValue: [] as string[],
  });
  protected readonly gitUrl = signal('');
  protected readonly gitBranch = signal('');
  protected readonly gitCommit = signal('');
  protected readonly gitToken = signal('');
  protected readonly selectedColor = signal(PROJECT_COLORS[0].value);
  protected readonly selectedFile = signal<File | null>(null);
  protected readonly dropzoneFiles = signal<DropzoneFile[]>([]);

  protected readonly colorOptions = PROJECT_COLORS;

  protected readonly submitted = signal(false);
  protected readonly nameError = computed(() => {
    if (!this.submitted()) return '';
    return this.projectName().trim() === ''
      ? this.translateService.instant('analyzeDialog.nameRequired')
      : '';
  });
  protected readonly languageError = computed(() => {
    if (!this.submitted() || this.uploadMethod() !== 'zip' || this.autoDetect()) return '';
    return this.selectedLanguages().length === 0
      ? this.translateService.instant('analyzeDialog.langRequired')
      : '';
  });
  protected readonly fileError = computed(() => {
    if (!this.submitted() || this.uploadMethod() !== 'zip') return '';
    return this.selectedFile() === null
      ? this.translateService.instant('analyzeDialog.fileRequired')
      : '';
  });
  protected readonly gitUrlError = computed(() => {
    if (!this.submitted() || this.uploadMethod() !== 'git') return '';
    const url = this.gitUrl().trim();
    if (url === '') return this.translateService.instant('analyzeDialog.gitUrlRequired');
    if (!isHttpUrl(url)) return this.translateService.instant('analyzeDialog.gitUrlInvalid');
    return '';
  });
  protected readonly gitCommitError = computed(() => {
    if (!this.submitted() || this.uploadMethod() !== 'git') return '';
    const commit = this.gitCommit().trim();
    return commit !== '' && !COMMIT_PATTERN.test(commit)
      ? this.translateService.instant('analyzeDialog.gitCommitInvalid')
      : '';
  });

  protected readonly loading = signal(false);
  protected readonly serverError = signal<string | null>(null);

  protected readonly canSubmit = computed(() => {
    if (this.loading() || this.projectName().trim() === '') return false;

    // A git analysis is shared, so it takes no language filter — only the
    // remote has to be describable, and the branch may be left to the backend
    if (this.uploadMethod() === 'git') {
      const commit = this.gitCommit().trim();
      return (
        isHttpUrl(this.gitUrl().trim()) && (commit === '' || COMMIT_PATTERN.test(commit))
      );
    }

    const languageChosen = this.autoDetect() || this.selectedLanguages().length > 0;
    return languageChosen && this.selectedFile() !== null;
  });

  protected languageLabel(code: string): string {
    return LANGUAGE_LABELS[code] ?? code.charAt(0).toUpperCase() + code.slice(1);
  }

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

  protected setUploadMethod(method: UploadMethod): void {
    this.uploadMethod.set(method);
    this.submitted.set(false);
    this.serverError.set(null);
  }

  protected onNameInput(value: string): void {
    this.projectName.set(value);
  }

  protected onGitUrlInput(value: string): void {
    this.gitUrl.set(value);
  }

  protected onGitBranchInput(value: string): void {
    this.gitBranch.set(value);
  }

  protected onGitCommitInput(value: string): void {
    this.gitCommit.set(value);
  }

  protected onGitTokenInput(value: string): void {
    this.gitToken.set(value);
  }

  protected toggleAutoDetect(): void {
    this.autoDetect.update((value) => !value);
    if (this.autoDetect()) {
      this.selectedLanguages.set([]);
    }
  }

  protected isLanguageSelected(code: string): boolean {
    return this.selectedLanguages().includes(code);
  }

  protected toggleLanguage(code: string): void {
    this.selectedLanguages.update((langs) =>
      langs.includes(code) ? langs.filter((l) => l !== code) : [...langs, code]
    );
  }

  protected selectColor(color: string): void {
    this.selectedColor.set(color);
  }

  protected onFileAdd(files: File[]): void {
    const file = files[0];
    if (file) {
      this.selectedFile.set(file);
      this.dropzoneFiles.set([
        {
          id: crypto.randomUUID(),
          name: file.name,
          size: file.size,
          status: 'uploaded',
        },
      ]);
    }
  }

  protected onSubmit(): void {
    this.submitted.set(true);
    this.serverError.set(null);

    if (!this.canSubmit()) return;

    const name = this.projectName().trim();
    const color = this.selectedColor();
    const isGit = this.uploadMethod() === 'git';

    this.loading.set(true);

    // Blank optional fields are dropped rather than sent: the backend reads an
    // empty branch as a branch to resolve, and would not find one
    const request$: Observable<AnalyzeFileResponse> = isGit
      ? this.repoService.analyzeGit({
          repo_url: this.gitUrl().trim(),
          name,
          color,
          branch: this.gitBranch().trim() || undefined,
          commit: this.gitCommit().trim() || undefined,
          token: this.gitToken().trim() || undefined,
        })
      : this.repoService.analyzeFile(
          this.selectedFile()!,
          name,
          this.autoDetect() ? [] : this.selectedLanguages(),
          color
        );

    request$
      .pipe(
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (response) => {
          this.repoStore.invalidateRepos();
          this.close();
          this.created.emit(response.repo_id);
        },
        error: (err: HttpErrorResponse) => {
          this.serverError.set(this.submitErrorMessage(err, isGit));
        },
      });
  }

  /**
   * Turn a failed create into something the user can act on.
   *
   * Each path has its own fixable causes: git has a repository they already
   * hold, a remote that refused the URL/branch/commit/token it was given, and
   * one that did not answer at all. A zip has only the archive — the dropzone
   * passes a dropped file through whatever `accept` says, so the backend is
   * where a non-zip is caught, and its 400 says nothing else here.
   */
  private submitErrorMessage(err: HttpErrorResponse, isGit: boolean): string {
    if (isGit) {
      switch (err.status) {
        case 409:
          return this.translateService.instant('analyzeDialog.gitAlreadyAdded');
        case 422:
          return this.translateService.instant('analyzeDialog.gitUnresolved');
        case 502:
          return this.translateService.instant('analyzeDialog.gitUnreachable');
      }
    } else if (err.status === 400) {
      return this.translateService.instant('analyzeDialog.zipRequired');
    }
    return this.translateService.instant('analyzeDialog.uploadFailed');
  }
}
