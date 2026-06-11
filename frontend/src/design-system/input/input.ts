import {
  booleanAttribute,
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  linkedSignal,
  numberAttribute,
  output,
  signal,
} from '@angular/core';

export type InputType = 'text' | 'textarea' | 'tel' | 'email' | 'password';
export type InputSize = '32' | '40';
export type InputTheme = 'light' | 'dark';

type InputState = 'default' | 'focus' | 'filled' | 'error' | 'disabled';

const COUNTRY_FLAGS: Record<string, string> = {
  FR: '🇫🇷',
  US: '🇺🇸',
  GB: '🇬🇧',
  DE: '🇩🇪',
  ES: '🇪🇸',
  IT: '🇮🇹',
};

const WRAPPER_BASE_CLASSES =
  'ui-input__wrapper relative flex items-center bg-background-default border border-border rounded-[var(--radius-xs)] shadow-light transition-all duration-150';

const WRAPPER_SIZE_CLASSES: Record<InputSize, string> = {
  '32': 'h-[var(--spacing-5xl)]',
  '40': 'h-[var(--spacing-7xl)]',
};

const WRAPPER_STATE_CLASSES: Record<InputState, Record<InputTheme, string>> = {
  default: { light: 'border-border', dark: 'bg-[var(--black-version-grey-800)] border-[var(--black-version-grey-500)]' },
  focus: {
    light: 'border-border-black shadow-[0_0_0_4px_var(--white-version-grey-450)]',
    dark: 'bg-[var(--black-version-grey-800)] border-[var(--black-version-grey-100)] shadow-[0_0_0_4px_var(--black-version-grey-500)]',
  },
  filled: { light: 'border-border', dark: 'bg-[var(--black-version-grey-800)] border-[var(--black-version-grey-500)]' },
  error: { light: 'border-border-error', dark: 'bg-[var(--black-version-grey-800)] border-border-error' },
  disabled: {
    light: 'bg-background-secondary cursor-not-allowed',
    dark: 'bg-[var(--black-version-grey-800)] border-[var(--black-version-grey-500)] opacity-60 cursor-not-allowed',
  },
};

const INPUT_BASE_CLASSES =
  'flex-1 h-full px-[var(--spacing-md)] border-none bg-transparent font-sans text-sm font-normal leading-6 outline-none';

const INPUT_THEME_CLASSES: Record<InputTheme, string> = {
  light: 'text-text-primary placeholder:text-text-tertiary',
  dark: 'text-[var(--black-version-grey-100)] placeholder:text-[var(--black-version-grey-300)]',
};

const LABEL_CLASSES: Record<InputTheme, string> = {
  light: 'text-sm font-medium leading-6 text-text-primary',
  dark: 'text-sm font-medium leading-6 text-[var(--black-version-grey-100)]',
};

const HELPER_CLASSES: Record<InputTheme, string> = {
  light: 'text-sm font-normal leading-6 text-text-secondary',
  dark: 'text-sm font-normal leading-6 text-[var(--black-version-grey-300)]',
};

const PHONE_SEPARATOR_CLASSES: Record<InputTheme, string> = {
  light: 'w-px h-full bg-border ml-[var(--spacing-md)]',
  dark: 'w-px h-full bg-[var(--black-version-grey-500)] ml-[var(--spacing-md)]',
};

const TEXTAREA_CLASSES = 'resize-y p-[var(--spacing-md)] min-h-[80px]';

@Component({
  selector: 'ui-input',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[attr.disabled]': 'disabled() ? "" : null',
  },
  template: `
    <div class="flex flex-col gap-[var(--spacing-xs)]">
      @if (label()) {
        <div class="flex justify-between items-center">
          <label class="{{ labelClass() }}" for="ui-input-field">{{ label() }}</label>
          @if (optional()) {
            <span class="text-sm font-normal leading-6 text-text-tertiary">(optional)</span>
          }
        </div>
      }

      @switch (type()) {
        @case ('textarea') {
          <div class="{{ wrapperClasses() }} h-auto min-h-[104px]">
            <textarea
              id="ui-input-field"
              class="{{ inputClasses() }} {{ textareaExtra }}"
              [value]="currentValue()"
              [placeholder]="placeholder() ?? ''"
              [disabled]="disabled()"
              [readOnly]="readonly()"
              [required]="required()"
              [attr.name]="name() ?? null"
              [attr.maxlength]="maxlength() ?? null"
              [rows]="rows()"
              (input)="onInput($event)"
              (focus)="onFocus()"
              (blur)="onBlur()"
            ></textarea>
          </div>
        }
        @case ('tel') {
          <div class="{{ wrapperClasses() }} pl-[var(--spacing-none)]">
            <div class="flex items-center gap-[var(--spacing-xs)] px-[var(--spacing-md)] h-full">
              <span class="text-base">{{ countryFlag() }}</span>
              <svg class="text-text-tertiary" width="6" height="4" viewBox="0 0 6 4" fill="none">
                <path d="M3 4L0 0H6L3 4Z" fill="currentColor"></path>
              </svg>
              <div class="{{ phoneSeparatorClass() }}"></div>
            </div>
            <input
              id="ui-input-field"
              type="tel"
              class="{{ inputClasses() }} pl-[var(--spacing-md)]"
              [value]="currentValue()"
              [placeholder]="placeholder() || '00 00 00 00 00'"
              [disabled]="disabled()"
              [readOnly]="readonly()"
              [required]="required()"
              [attr.name]="name() ?? null"
              [attr.maxlength]="maxlength() ?? null"
              (input)="onInput($event)"
              (focus)="onFocus()"
              (blur)="onBlur()"
            />
            @if (filled() && !error()) {
              <svg
                class="absolute right-[var(--spacing-md)] top-1/2 -translate-y-1/2"
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
              >
                <circle cx="12" cy="12" r="10" fill="var(--success-success-300)"></circle>
                <path
                  d="M8 12L11 15L16 9"
                  stroke="white"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                ></path>
              </svg>
            }
          </div>
        }
        @default {
          <div class="{{ wrapperClasses() }}">
            <input
              id="ui-input-field"
              [type]="type()"
              class="{{ inputClasses() }}"
              [value]="currentValue()"
              [placeholder]="placeholder() ?? ''"
              [disabled]="disabled()"
              [readOnly]="readonly()"
              [required]="required()"
              [attr.name]="name() ?? null"
              [attr.maxlength]="maxlength() ?? null"
              (input)="onInput($event)"
              (focus)="onFocus()"
              (blur)="onBlur()"
            />
          </div>
        }
      }

      @if (error()) {
        <span class="text-sm font-normal leading-6 text-danger">{{ error() }}</span>
      } @else if (helper()) {
        <span class="{{ helperClass() }}">{{ helper() }}</span>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
    }

    :host([disabled]) {
      pointer-events: none;
    }
  `,
})
export class UiInput {
  readonly type = input<InputType>('text');
  readonly label = input<string>();
  readonly placeholder = input<string>();
  readonly value = input('');
  readonly size = input<InputSize>('40');
  readonly disabled = input(false, { transform: booleanAttribute });
  readonly error = input<string>();
  readonly helper = input<string>();
  readonly optional = input(false, { transform: booleanAttribute });
  readonly theme = input<InputTheme>('light');
  readonly rows = input(4, { transform: numberAttribute });
  readonly countryCode = input('FR');
  readonly name = input<string>();
  readonly required = input(false, { transform: booleanAttribute });
  readonly readonly = input(false, { transform: booleanAttribute });
  readonly maxlength = input<number | undefined, unknown>(undefined, {
    transform: numberAttribute,
  });

  readonly valueChange = output<string>();

  protected readonly textareaExtra = TEXTAREA_CLASSES;

  protected readonly currentValue = linkedSignal(() => this.value());
  protected readonly isFocused = signal(false);
  protected readonly filled = computed(() => this.currentValue().length > 0);

  protected readonly inputState = computed<InputState>(() => {
    if (this.disabled()) return 'disabled';
    if (this.error()) return 'error';
    if (this.isFocused()) return 'focus';
    if (this.filled()) return 'filled';
    return 'default';
  });

  protected readonly wrapperClasses = computed(() =>
    [WRAPPER_BASE_CLASSES, WRAPPER_SIZE_CLASSES[this.size()], WRAPPER_STATE_CLASSES[this.inputState()][this.theme()]].join(
      ' ',
    ),
  );

  protected readonly inputClasses = computed(() => {
    const classes = [INPUT_BASE_CLASSES, INPUT_THEME_CLASSES[this.theme()]];
    if (this.disabled()) classes.push('cursor-not-allowed');
    return classes.join(' ');
  });

  protected readonly labelClass = computed(() => LABEL_CLASSES[this.theme()]);
  protected readonly helperClass = computed(() => HELPER_CLASSES[this.theme()]);
  protected readonly phoneSeparatorClass = computed(() => PHONE_SEPARATOR_CLASSES[this.theme()]);
  protected readonly countryFlag = computed(() => COUNTRY_FLAGS[this.countryCode()] || '🏳️');

  protected onInput(event: Event): void {
    const value = (event.target as HTMLInputElement | HTMLTextAreaElement).value;
    this.currentValue.set(value);
    this.valueChange.emit(value);
  }

  protected onFocus(): void {
    this.isFocused.set(true);
  }

  protected onBlur(): void {
    this.isFocused.set(false);
  }
}
