import { NgTemplateOutlet } from '@angular/common';
import {
  booleanAttribute,
  ChangeDetectionStrategy,
  Component,
  computed,
  ElementRef,
  input,
  linkedSignal,
  numberAttribute,
  output,
  signal,
  viewChild,
} from '@angular/core';

export type DropzoneTheme = 'light' | 'dark';
export type FileStatus = 'uploading' | 'uploaded' | 'error' | 'failed';

export interface DropzoneFile {
  id: string;
  name: string;
  size: number;
  status: FileStatus;
  progress?: number;
  error?: string;
}

const DROP_AREA_BASE_CLASSES =
  'relative flex flex-col items-center justify-center py-[var(--spacing-4xl)] px-[var(--spacing-2xl)] rounded-[var(--radius-xs)] cursor-pointer transition-all duration-150';

const DROP_AREA_STATE_CLASSES: Record<string, Record<DropzoneTheme, string>> = {
  default: { light: 'bg-transparent', dark: 'bg-transparent' },
  hover: {
    light: 'border border-dashed border-border-black bg-background-secondary',
    dark: 'border border-dashed border-text-inverse bg-background-inverse/5',
  },
  dragover: {
    light: 'border border-dashed border-border-black bg-background-secondary',
    dark: 'border border-dashed border-text-inverse bg-background-inverse/5',
  },
  error: {
    light: 'border border-dashed border-border bg-transparent',
    dark: 'border border-dashed border-border bg-transparent',
  },
};

const TEXT_CLASSES = {
  uploadIcon: { light: 'text-text-tertiary', dark: 'text-text-tertiary' } as Record<DropzoneTheme, string>,
  instruction: {
    light: 'text-xs leading-relaxed text-text-tertiary text-center mt-[var(--spacing-md)]',
    dark: 'text-xs leading-relaxed text-text-tertiary text-center mt-[var(--spacing-md)]',
  } as Record<DropzoneTheme, string>,
  dragOver: {
    light: 'text-sm font-medium text-text-primary text-center',
    dark: 'text-sm font-medium text-text-inverse text-center',
  } as Record<DropzoneTheme, string>,
  dragOverSubtext: {
    light: 'text-xs text-text-tertiary text-center mt-[var(--spacing-xs)]',
    dark: 'text-xs text-text-tertiary text-center mt-[var(--spacing-xs)]',
  } as Record<DropzoneTheme, string>,
  error: 'text-xs leading-relaxed text-[var(--color-text-danger)] text-center mt-[var(--spacing-md)]',
};

const FILE_ITEM_BASE_CLASSES =
  'flex items-center gap-[var(--spacing-xl)] px-[var(--spacing-xl)] py-[var(--spacing-md)] rounded-[var(--radius-xs)] transition-all duration-150';

const FILE_ITEM_STATE_CLASSES: Record<FileStatus, Record<DropzoneTheme, string>> = {
  uploaded: { light: 'bg-background-secondary', dark: 'bg-background-inverse/10' },
  uploading: { light: 'bg-background-secondary border border-border', dark: 'bg-background-inverse/10 border border-border' },
  error: { light: 'bg-background-secondary border border-border', dark: 'bg-background-inverse/10 border border-border' },
  failed: { light: 'bg-background-secondary border border-border', dark: 'bg-background-inverse/10 border border-border' },
};

const FILE_ELEMENT_CLASSES = {
  icon: { light: 'text-text-tertiary flex-shrink-0', dark: 'text-text-tertiary flex-shrink-0' } as Record<DropzoneTheme, string>,
  errorIcon: 'text-[var(--color-text-danger)] flex-shrink-0',
  name: { light: 'text-sm font-medium text-text-primary truncate', dark: 'text-sm font-medium text-text-inverse truncate' } as Record<
    DropzoneTheme,
    string
  >,
  errorName: 'text-sm font-medium text-[var(--color-text-danger)] underline truncate',
  size: { light: 'text-xs text-text-tertiary flex-shrink-0', dark: 'text-xs text-text-tertiary flex-shrink-0' } as Record<
    DropzoneTheme,
    string
  >,
  actions: 'flex items-center gap-[var(--spacing-xs)] ml-auto flex-shrink-0',
};

const BUTTON_CLASSES = {
  action: {
    light:
      'p-[var(--spacing-sm)] rounded-[var(--radius-xs)] text-text-tertiary hover:bg-[var(--white-version-grey-200)] hover:text-text-primary transition-colors duration-150 cursor-pointer border-none bg-transparent',
    dark: 'p-[var(--spacing-sm)] rounded-[var(--radius-xs)] text-text-tertiary hover:bg-background-inverse/10 hover:text-text-inverse transition-colors duration-150 cursor-pointer border-none bg-transparent',
  } as Record<DropzoneTheme, string>,
  cancel: {
    light:
      'inline-flex items-center gap-[var(--spacing-xs)] px-[var(--spacing-md)] py-[var(--spacing-xs)] text-xs font-medium text-text-primary bg-transparent border border-border rounded-[var(--radius-xs)] hover:bg-background-secondary hover:border-border-black transition-all duration-150 cursor-pointer',
    dark: 'inline-flex items-center gap-[var(--spacing-xs)] px-[var(--spacing-md)] py-[var(--spacing-xs)] text-xs font-medium text-text-inverse bg-transparent border border-border rounded-[var(--radius-xs)] hover:bg-background-inverse/10 hover:border-text-inverse transition-all duration-150 cursor-pointer',
  } as Record<DropzoneTheme, string>,
  retry: {
    light:
      'inline-flex items-center gap-[var(--spacing-xs)] px-[var(--spacing-md)] py-[var(--spacing-xs)] text-xs font-medium text-text-primary bg-transparent border border-border rounded-[var(--radius-xs)] hover:bg-background-secondary hover:border-border-black transition-all duration-150 cursor-pointer',
    dark: 'inline-flex items-center gap-[var(--spacing-xs)] px-[var(--spacing-md)] py-[var(--spacing-xs)] text-xs font-medium text-text-inverse bg-transparent border border-border rounded-[var(--radius-xs)] hover:bg-background-inverse/10 hover:border-text-inverse transition-all duration-150 cursor-pointer',
  } as Record<DropzoneTheme, string>,
};

const PROGRESS_CLASSES = {
  container: 'flex-1 flex items-center gap-[var(--spacing-md)]',
  track: {
    light: 'flex-1 h-[var(--spacing-xxs)] bg-border rounded-full overflow-hidden',
    dark: 'flex-1 h-[var(--spacing-xxs)] bg-border rounded-full overflow-hidden',
  } as Record<DropzoneTheme, string>,
  fill: {
    light: 'h-full bg-text-primary transition-all duration-300',
    dark: 'h-full bg-text-inverse transition-all duration-300',
  } as Record<DropzoneTheme, string>,
  text: {
    light: 'text-xs text-text-tertiary w-[var(--spacing-5xl)] text-right',
    dark: 'text-xs text-text-tertiary w-[var(--spacing-5xl)] text-right',
  } as Record<DropzoneTheme, string>,
};

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(0)} Mo`;
}

@Component({
  selector: 'ui-dropzone',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[attr.disabled]': 'disabled() ? "" : null',
    '(dragenter)': 'onDragEnter($event)',
    '(dragleave)': 'onDragLeave($event)',
    '(dragover)': 'onDragOver($event)',
    '(drop)': 'onDrop($event)',
  },
  template: `
    <div class="flex flex-col gap-[var(--spacing-xl)]">
      <div
        class="{{ dropAreaClasses() }}"
        (click)="handleClick()"
        (mouseenter)="isHovered.set(true)"
        (mouseleave)="isHovered.set(false)"
        role="button"
        tabindex="0"
        aria-label="Zone de dépôt de fichiers"
      >
        <input
          #fileInput
          type="file"
          class="ui-dropzone__input"
          [attr.accept]="accept()"
          [multiple]="multiple()"
          [disabled]="disabled()"
          (change)="onInputChange($event)"
        />
        @if (isDragOver()) {
          <div class="flex flex-col items-center">
            <span class="{{ TEXT.uploadIcon[theme()] }}">
              <ng-container [ngTemplateOutlet]="uploadIcon"></ng-container>
            </span>
            <span class="{{ TEXT.dragOver[theme()] }} mt-[var(--spacing-xl)]">Déposez votre fichier ici</span>
            <span class="{{ TEXT.dragOverSubtext[theme()] }}"
              >Nous acceptons les fichiers : {{ acceptedFormats() }}. jusqu'à {{ maxSizeLabel() }}.</span
            >
          </div>
        } @else {
          <div class="flex flex-col items-center">
            <span class="{{ TEXT.uploadIcon[theme()] }}">
              <ng-container [ngTemplateOutlet]="uploadIcon"></ng-container>
            </span>
            @if (errorState()) {
              <span class="{{ TEXT.error }}">{{ errorState() }}</span>
            } @else {
              <span class="{{ TEXT.instruction[theme()] }}">{{ defaultInstructionText() }}</span>
            }
          </div>
        }
      </div>

      @if (files().length > 0) {
        <div class="flex flex-col gap-[var(--spacing-md)]">
          @for (file of files(); track file.id) {
            @switch (file.status) {
              @case ('uploaded') {
                <div class="{{ FILE_ITEM_BASE }} {{ FILE_ITEM_STATE.uploaded[theme()] }}">
                  <span class="{{ FILE_EL.icon[theme()] }}"><ng-container [ngTemplateOutlet]="fileIcon"></ng-container></span>
                  <span class="{{ FILE_EL.name[theme()] }}">{{ file.name }}</span>
                  <span class="{{ FILE_EL.size[theme()] }}">{{ formatSize(file.size) }}</span>
                  <div class="{{ FILE_EL.actions }}">
                    <button class="{{ BUTTON.action[theme()] }}" (click)="onRemove(file)" aria-label="Supprimer le fichier">
                      <ng-container [ngTemplateOutlet]="deleteIcon"></ng-container>
                    </button>
                    <button class="{{ BUTTON.action[theme()] }}" aria-label="Voir le fichier">
                      <ng-container [ngTemplateOutlet]="viewIcon"></ng-container>
                    </button>
                  </div>
                </div>
              }
              @case ('uploading') {
                <div class="{{ FILE_ITEM_BASE }} {{ FILE_ITEM_STATE.uploading[theme()] }}">
                  <span class="{{ FILE_EL.icon[theme()] }}"><ng-container [ngTemplateOutlet]="fileIcon"></ng-container></span>
                  <span class="{{ FILE_EL.name[theme()] }}">{{ file.name }}</span>
                  <div class="{{ PROGRESS.container }}">
                    <div class="{{ PROGRESS.track[theme()] }}">
                      <div class="{{ PROGRESS.fill[theme()] }}" [style.width.%]="file.progress ?? 0"></div>
                    </div>
                    <span class="{{ PROGRESS.text[theme()] }}">{{ file.progress ?? 0 }}%</span>
                  </div>
                  <button class="{{ BUTTON.cancel[theme()] }}" (click)="onCancel(file)">
                    Annuler<ng-container [ngTemplateOutlet]="closeIcon"></ng-container>
                  </button>
                </div>
              }
              @case ('error') {
                <div class="{{ FILE_ITEM_BASE }} {{ FILE_ITEM_STATE.error[theme()] }}">
                  <span class="{{ FILE_EL.errorIcon }}"><ng-container [ngTemplateOutlet]="fileIcon"></ng-container></span>
                  <span class="{{ FILE_EL.errorName }}">{{ file.error || "Quelque chose s'est mal passé" }}</span>
                  <div class="{{ FILE_EL.actions }}">
                    <button class="{{ BUTTON.action[theme()] }}" (click)="onRemove(file)" aria-label="Supprimer le fichier">
                      <ng-container [ngTemplateOutlet]="deleteIcon"></ng-container>
                    </button>
                    <button class="{{ BUTTON.action[theme()] }}" aria-label="Voir le fichier">
                      <ng-container [ngTemplateOutlet]="viewIcon"></ng-container>
                    </button>
                  </div>
                </div>
              }
              @case ('failed') {
                <div class="{{ FILE_ITEM_BASE }} {{ FILE_ITEM_STATE.failed[theme()] }}">
                  <span class="{{ FILE_EL.icon[theme()] }}"><ng-container [ngTemplateOutlet]="fileIcon"></ng-container></span>
                  <span class="{{ FILE_EL.name[theme()] }}">{{ file.name }}</span>
                  <div class="ml-auto">
                    <button class="{{ BUTTON.retry[theme()] }}" (click)="onRetry(file)">
                      Recommencer<ng-container [ngTemplateOutlet]="retryIcon"></ng-container>
                    </button>
                  </div>
                </div>
              }
            }
          }
        </div>
      }
    </div>

    <ng-template #uploadIcon>
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="17 8 12 3 7 8"></polyline>
        <line x1="12" y1="3" x2="12" y2="15"></line>
      </svg>
    </ng-template>
    <ng-template #fileIcon>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
        <polyline points="14 2 14 8 20 8"></polyline>
      </svg>
    </ng-template>
    <ng-template #deleteIcon>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="3 6 5 6 21 6"></polyline>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
      </svg>
    </ng-template>
    <ng-template #viewIcon>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
        <circle cx="12" cy="12" r="3"></circle>
      </svg>
    </ng-template>
    <ng-template #closeIcon>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
    </ng-template>
    <ng-template #retryIcon>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="23 4 23 10 17 10"></polyline>
        <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
      </svg>
    </ng-template>
  `,
  styles: `
    :host {
      display: block;
    }

    :host([disabled]) {
      pointer-events: none;
    }

    .ui-dropzone__input {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }
  `,
  imports: [NgTemplateOutlet],
})
export class UiDropzone {
  readonly theme = input<DropzoneTheme>('light');
  readonly accept = input('.docx,.pdf,.pptx,.xlsx');
  readonly maxSize = input(50 * 1024 * 1024, { transform: numberAttribute });
  readonly multiple = input(true, { transform: booleanAttribute });
  readonly disabled = input(false, { transform: booleanAttribute });
  readonly files = input<DropzoneFile[]>([]);
  readonly error = input<string>();
  readonly instructionText = input<string>();

  readonly fileAdd = output<File[]>();
  readonly fileRemove = output<DropzoneFile>();
  readonly fileCancel = output<DropzoneFile>();
  readonly fileRetry = output<DropzoneFile>();

  protected readonly TEXT = TEXT_CLASSES;
  protected readonly FILE_ITEM_BASE = FILE_ITEM_BASE_CLASSES;
  protected readonly FILE_ITEM_STATE = FILE_ITEM_STATE_CLASSES;
  protected readonly FILE_EL = FILE_ELEMENT_CLASSES;
  protected readonly BUTTON = BUTTON_CLASSES;
  protected readonly PROGRESS = PROGRESS_CLASSES;
  protected readonly formatSize = formatFileSize;

  protected readonly isDragOver = signal(false);
  protected readonly isHovered = signal(false);
  protected readonly errorState = linkedSignal(() => this.error());

  private readonly fileInputRef = viewChild<ElementRef<HTMLInputElement>>('fileInput');

  protected readonly acceptedFormats = computed(() =>
    this.accept()
      .split(',')
      .map((ext) => ext.trim().replace('.', ''))
      .join(', '),
  );

  protected readonly maxSizeLabel = computed(() => formatFileSize(this.maxSize()));

  protected readonly defaultInstructionText = computed(
    () =>
      this.instructionText() ||
      `Faites glisser les fichiers ici ou cliquez pour sélectionner des fichiers sur votre ordinateur. Nous acceptons les fichiers : ${this.acceptedFormats()}. jusqu'à ${this.maxSizeLabel()}.`,
  );

  protected readonly dropAreaClasses = computed(() => {
    let state = 'default';
    if (this.errorState()) state = 'error';
    else if (this.isDragOver()) state = 'dragover';
    else if (this.isHovered()) state = 'hover';

    const classes = [DROP_AREA_BASE_CLASSES, DROP_AREA_STATE_CLASSES[state][this.theme()]];
    if (this.disabled()) classes.push('opacity-50 cursor-not-allowed');
    return classes.join(' ');
  });

  protected onDragEnter(event: DragEvent): void {
    if (this.disabled()) return;
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(true);
  }

  protected onDragLeave(event: DragEvent): void {
    if (this.disabled()) return;
    event.preventDefault();
    event.stopPropagation();
    const relatedTarget = event.relatedTarget as Node | null;
    if (relatedTarget && (event.currentTarget as Node).contains(relatedTarget)) return;
    this.isDragOver.set(false);
  }

  protected onDragOver(event: DragEvent): void {
    if (this.disabled()) return;
    event.preventDefault();
    event.stopPropagation();
  }

  protected onDrop(event: DragEvent): void {
    if (this.disabled()) return;
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(false);
    const files = event.dataTransfer?.files;
    if (files && files.length > 0) this.processFiles(files);
  }

  protected handleClick(): void {
    if (this.disabled()) return;
    this.fileInputRef()?.nativeElement.click();
  }

  protected onInputChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = input.files;
    if (files && files.length > 0) this.processFiles(files);
    input.value = '';
  }

  private processFiles(fileList: FileList): void {
    const newFiles: File[] = [];
    const errors: string[] = [];

    for (const file of Array.from(fileList)) {
      if (file.size > this.maxSize()) {
        errors.push(`${file.name} dépasse la taille maximale autorisée`);
        continue;
      }
      newFiles.push(file);
    }

    this.errorState.set(errors.length > 0 ? errors[0] : undefined);

    if (newFiles.length > 0) {
      this.fileAdd.emit(newFiles);
    }
  }

  protected onRemove(file: DropzoneFile): void {
    this.fileRemove.emit(file);
  }

  protected onCancel(file: DropzoneFile): void {
    this.fileCancel.emit(file);
  }

  protected onRetry(file: DropzoneFile): void {
    this.fileRetry.emit(file);
  }
}
