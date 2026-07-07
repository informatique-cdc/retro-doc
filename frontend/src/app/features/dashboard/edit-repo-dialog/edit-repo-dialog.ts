import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { finalize } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { UiButton, UiInput } from '@design-system';
import { Repo, RepoService, RepoStore } from '../../../core/api';

interface ColorOption {
  value: string;
  label: string;
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

const LANGUAGE_LABELS: Record<string, string> = {
  java: 'Java',
  python: 'Python',
  typescript: 'TypeScript',
  cobol: 'COBOL',
};

@Component({
  selector: 'app-edit-repo-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule, UiButton, UiInput],
  templateUrl: './edit-repo-dialog.html',
  styleUrl: './edit-repo-dialog.scss',
})
export class EditRepoDialog implements AfterViewInit {
  readonly repo = input.required<Repo>();
  readonly closed = output<void>();
  readonly saved = output<void>();
  readonly deleted = output<void>();

  private readonly repoService = inject(RepoService);
  private readonly repoStore = inject(RepoStore);
  private readonly destroyRef = inject(DestroyRef);
  private readonly translateService = inject(TranslateService);

  private readonly dialogRef = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  protected readonly repoName = signal('');
  protected readonly selectedColor = signal('');
  protected readonly loading = signal(false);
  protected readonly serverError = signal<string | null>(null);
  protected readonly confirmingDelete = signal(false);
  protected readonly deleting = signal(false);

  protected readonly colorOptions = PROJECT_COLORS;

  ngAfterViewInit(): void {
    this.repoName.set(this.repo().name);
    this.selectedColor.set(this.repo().color ?? PROJECT_COLORS[0].value);
    this.dialogRef().nativeElement.showModal();
  }

  protected languageLabel(): string {
    return this.repo()
      .languages.map((code) => LANGUAGE_LABELS[code] ?? code)
      .join(', ');
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

  protected selectColor(color: string): void {
    this.selectedColor.set(color);
  }

  protected onNameInput(value: string): void {
    this.repoName.set(value);
  }

  protected onSubmit(): void {
    this.serverError.set(null);
    this.loading.set(true);

    const name = this.repoName().trim();
    const color = this.selectedColor();

    this.repoService
      .updateUserRepo(this.repo().repo_id, { name, color })
      .pipe(
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: () => {
          this.repoStore.invalidateRepos();
          this.close();
          this.saved.emit();
        },
        error: () => {
          this.serverError.set(
            this.translateService.instant('editRepoDialog.saveFailed')
          );
        },
      });
  }

  protected requestDelete(): void {
    this.serverError.set(null);
    this.confirmingDelete.set(true);
  }

  protected cancelDelete(): void {
    this.confirmingDelete.set(false);
  }

  protected confirmDelete(): void {
    this.serverError.set(null);
    this.deleting.set(true);

    this.repoService
      .deleteRepo(this.repo().repo_id)
      .pipe(
        finalize(() => this.deleting.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: () => {
          this.repoStore.invalidateRepos();
          this.close();
          this.deleted.emit();
        },
        error: () => {
          this.serverError.set(
            this.translateService.instant('editRepoDialog.deleteFailed')
          );
        },
      });
  }
}
