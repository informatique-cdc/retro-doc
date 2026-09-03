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
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { finalize } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { UiButton, UiDropzone, UiInput } from '@design-system';
import { RepoService, RepoStore } from '../../../core/api';

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
    if (!this.submitted() || this.autoDetect()) return '';
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
    return this.gitUrl().trim() === ''
      ? this.translateService.instant('analyzeDialog.gitUrlRequired')
      : '';
  });

  protected readonly loading = signal(false);
  protected readonly serverError = signal<string | null>(null);

  protected readonly canSubmit = computed(() => {
    const languageChosen = this.autoDetect() || this.selectedLanguages().length > 0;
    const baseFilled =
      this.projectName().trim() !== '' && languageChosen && !this.loading();

    if (this.uploadMethod() === 'git') {
      return baseFilled && this.gitUrl().trim() !== '';
    }
    return baseFilled && this.selectedFile() !== null;
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
    const languages = this.autoDetect() ? [] : this.selectedLanguages();

    this.loading.set(true);

    const color = this.selectedColor();

    const request$ =
      this.uploadMethod() === 'git'
        ? this.repoService.analyzeGitUrl(this.gitUrl().trim(), name, languages, color)
        : this.repoService.analyzeFile(this.selectedFile()!, name, languages, color);

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
        error: () => {
          this.serverError.set(this.translateService.instant('analyzeDialog.uploadFailed'));
        },
      });
  }
}
