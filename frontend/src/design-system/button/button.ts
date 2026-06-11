import { NgTemplateOutlet } from '@angular/common';
import {
  booleanAttribute,
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
} from '@angular/core';

export type ButtonVariant = 'primary' | 'secondary' | 'tertiary' | 'danger' | 'icon' | 'link';
export type ButtonSize = 'small' | 'medium' | 'large';
export type ButtonTheme = 'light' | 'dark';

const baseClasses = `
  inline-flex items-center justify-center gap-[var(--spacing-xs)]
  border-none cursor-pointer
  font-sans font-medium
  no-underline transition-all duration-150
  box-border overflow-hidden
  focus-visible:outline-2 focus-visible:outline-border-focus focus-visible:outline-offset-2
`;

const sizeClasses: Record<ButtonSize, string> = {
  small: 'h-[var(--spacing-5xl)] px-[var(--spacing-xl)] py-[var(--spacing-sm)] text-xs leading-sm rounded-[var(--radius-xs)]',
  medium: 'h-[var(--spacing-7xl)] px-[var(--spacing-xl)] py-[var(--spacing-md)] text-xs leading-sm rounded-[var(--radius-xs)]',
  large: 'h-[var(--spacing-8xl)] px-[var(--spacing-2xl)] py-[var(--spacing-xl)] text-sm leading-sm rounded-[var(--radius-xs)]',
};

const iconSizeClasses: Record<ButtonSize, string> = {
  small: 'w-[var(--spacing-5xl)] h-[var(--spacing-5xl)] p-[var(--spacing-none)] rounded-[var(--radius-xs)]',
  medium: 'w-[var(--spacing-7xl)] h-[var(--spacing-7xl)] p-[var(--spacing-none)] rounded-[var(--radius-xs)]',
  large: 'w-[var(--spacing-8xl)] h-[var(--spacing-8xl)] p-[var(--spacing-none)] rounded-[var(--radius-xs)]',
};

const variantClasses: Record<ButtonVariant, Record<ButtonTheme, string>> = {
  primary: {
    light: `
      bg-gradient-to-b from-[var(--white-version-grey-600)]/50 to-[var(--white-version-grey-600)]
      border border-border-black
      text-text-inverse
      shadow-light
      hover:from-[var(--white-version-grey-600)] hover:to-[var(--white-version-grey-600)]
      active:from-[var(--white-version-grey-600)] active:to-[var(--white-version-grey-600)] active:[&>*]:opacity-50
    `,
    dark: `
      bg-gradient-to-b from-text-inverse/10 to-text-inverse
      border border-text-inverse
      text-[var(--black-version-grey-600)]
      shadow-light
      hover:from-text-inverse hover:to-text-inverse
      active:from-text-inverse active:to-text-inverse active:[&>*]:opacity-50
    `,
  },
  secondary: {
    light: `
      bg-background-secondary
      border-none
      text-text-secondary
      hover:bg-[var(--white-version-grey-200)]
      active:bg-background-secondary active:[&>*]:opacity-50
    `,
    dark: `
      bg-background-secondary
      border-none
      text-text-inverse
      hover:bg-background-selected
      active:bg-background-secondary active:[&>*]:opacity-50
    `,
  },
  tertiary: {
    light: `
      bg-background-default
      border border-border
      text-text-primary
      shadow-light
      hover:bg-[var(--white-version-grey-200)] hover:border-transparent hover:shadow-none
      active:bg-[var(--white-version-grey-200)] active:border-transparent active:shadow-none active:[&>*]:opacity-50
    `,
    dark: `
      bg-transparent
      border border-border
      text-text-inverse
      hover:bg-text-inverse/10 hover:border-text-inverse
      active:bg-text-inverse/10 active:[&>*]:opacity-50
    `,
  },
  danger: {
    light: `
      bg-background-default
      border border-border
      text-[var(--danger-danger-300)]
      shadow-light
      hover:bg-background-secondary hover:border-transparent hover:shadow-none
      active:bg-background-secondary active:border-transparent active:shadow-none active:[&>*]:opacity-50
    `,
    dark: `
      bg-transparent
      border border-[var(--danger-danger-300)]
      text-[var(--danger-danger-200)]
      hover:bg-[var(--danger-danger-300)]/20
      active:bg-[var(--danger-danger-300)]/20 active:[&>*]:opacity-50
    `,
  },
  icon: {
    light: `
      bg-background-default
      border border-border
      text-text-primary
      shadow-light
      hover:bg-[var(--white-version-grey-200)] hover:border-transparent hover:shadow-none
      active:bg-[var(--white-version-grey-200)] active:border-transparent active:shadow-none active:[&>*]:opacity-50
    `,
    dark: `
      bg-background-secondary
      border border-border
      text-text-inverse
      hover:bg-background-selected
      active:bg-background-selected active:[&>*]:opacity-50
    `,
  },
  link: {
    light: `
      bg-transparent
      border-none
      p-[var(--spacing-none)] h-auto
      text-text-primary
      text-sm font-medium leading-6
      rounded-[var(--radius-none)]
      relative
      hover:text-text-tertiary
      active:text-text-tertiary active:opacity-50
    `,
    dark: `
      bg-transparent
      border-none
      p-[var(--spacing-none)] h-auto
      text-text-inverse
      text-sm font-medium leading-6
      rounded-[var(--radius-none)]
      relative
      hover:text-[var(--black-version-grey-400)]
      active:text-[var(--black-version-grey-400)] active:opacity-50
    `,
  },
};

const stateClasses = {
  disabled: 'opacity-50 cursor-not-allowed pointer-events-none',
  loading: 'cursor-wait pointer-events-none',
};

@Component({
  selector: 'ui-button',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (href() && !disabled()) {
      <a
        [class]="rootClasses()"
        [href]="href()"
        [attr.aria-disabled]="disabled()"
        (click)="handleClick($event)"
      >
        <ng-container [ngTemplateOutlet]="content"></ng-container>
      </a>
    } @else {
      <button
        [class]="rootClasses()"
        [type]="type()"
        [disabled]="disabled()"
        [attr.aria-busy]="loading()"
        (click)="handleClick($event)"
      >
        <ng-container [ngTemplateOutlet]="content"></ng-container>
      </button>
    }

    <ng-template #content>
      @if (loading()) {
        <svg class="ui-button-spinner" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <circle
            cx="12"
            cy="12"
            r="10"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-dasharray="31.4 31.4"
          ></circle>
        </svg>
        <span class="ui-button-sr-only">Loading...</span>
      } @else {
        <ng-content select="[slot=icon-left]"></ng-content>
        <span class="ui-button-label"><ng-content></ng-content></span>
        <ng-content select="[slot=icon-right]"></ng-content>
      }
      @if (isLink()) {
        <span
          class="absolute bottom-0 left-0 right-0 h-[var(--spacing-xxs)] {{ underlineClass() }}"
        ></span>
      }
    </ng-template>
  `,
  styles: `
    :host {
      display: inline-block;
    }

    .ui-button-sr-only {
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

    .ui-button-label {
      display: inline-flex;
      align-items: center;
    }

    .ui-button-spinner {
      width: var(--spacing-3xl);
      height: var(--spacing-3xl);
      animation: ui-button-spin 1s linear infinite;
    }

    @keyframes ui-button-spin {
      from {
        transform: rotate(0deg);
      }
      to {
        transform: rotate(360deg);
      }
    }

    :host ::ng-deep svg,
    :host ::ng-deep img {
      width: var(--spacing-3xl);
      height: var(--spacing-3xl);
    }
  `,
  imports: [NgTemplateOutlet],
})
export class UiButton {
  readonly variant = input<ButtonVariant>('primary');
  readonly size = input<ButtonSize>('medium');
  readonly theme = input<ButtonTheme>('light');
  readonly disabled = input(false, { transform: booleanAttribute });
  readonly loading = input(false, { transform: booleanAttribute });
  readonly href = input<string>();
  readonly iconOnly = input(false, { transform: booleanAttribute });
  readonly type = input<'button' | 'submit' | 'reset'>('button');

  protected readonly isIconMode = computed(() => this.iconOnly() || this.variant() === 'icon');
  protected readonly isLink = computed(() => this.variant() === 'link');

  protected readonly underlineClass = computed(() =>
    this.theme() === 'light' ? 'bg-[#e1e1e1]' : 'bg-[var(--black-version-grey-400)]',
  );

  protected readonly rootClasses = computed(() => {
    const classes = [baseClasses, variantClasses[this.variant()][this.theme()]];

    if (!this.isLink()) {
      classes.push(this.isIconMode() ? iconSizeClasses[this.size()] : sizeClasses[this.size()]);
    }
    if (this.disabled()) classes.push(stateClasses.disabled);
    if (this.loading()) classes.push(stateClasses.loading);
    if (this.isLink()) classes.push('group');

    return classes.join(' ').replace(/\s+/g, ' ').trim();
  });

  protected handleClick(event: Event): void {
    if (this.disabled() || this.loading()) {
      event.preventDefault();
      event.stopPropagation();
    }
  }
}
