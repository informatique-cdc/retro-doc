import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

export type SpinnerSize = 'small' | 'medium' | 'large';
export type SpinnerTheme = 'light' | 'dark';

const SIZE_CONFIG: Record<
  SpinnerSize,
  { dimension: number; strokeWidth: number; className: string }
> = {
  small: { dimension: 16, strokeWidth: 2, className: 'w-[var(--spacing-2xl)] h-[var(--spacing-2xl)]' },
  medium: { dimension: 24, strokeWidth: 2, className: 'w-[var(--spacing-4xl)] h-[var(--spacing-4xl)]' },
  large: { dimension: 32, strokeWidth: 3, className: 'w-[var(--spacing-5xl)] h-[var(--spacing-5xl)]' },
};

const THEME_CLASSES: Record<SpinnerTheme, string> = {
  light: 'text-[var(--white-version-grey-450)]',
  dark: 'text-text-inverse',
};

@Component({
  selector: 'ui-spinner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @let c = config();
    <div class="inline-flex items-center justify-center box-border {{ c.className }}">
      <svg
        class="ui-spinner-svg {{ c.themeClass }}"
        [attr.width]="c.dimension"
        [attr.height]="c.dimension"
        [attr.viewBox]="c.viewBox"
        xmlns="http://www.w3.org/2000/svg"
        [attr.aria-label]="ariaLabel()"
        role="status"
      >
        <circle
          [attr.cx]="c.center"
          [attr.cy]="c.center"
          [attr.r]="c.radius"
          fill="none"
          stroke="currentColor"
          [attr.stroke-width]="c.strokeWidth"
          stroke-linecap="round"
          [attr.stroke-dasharray]="c.dashArray"
          [attr.stroke-dashoffset]="c.dashOffset"
        ></circle>
      </svg>
    </div>
  `,
  styles: `
    :host {
      display: inline-block;
    }

    .ui-spinner-svg {
      animation: ui-spinner-spin 1s linear infinite;
    }

    @keyframes ui-spinner-spin {
      from {
        transform: rotate(0deg);
      }
      to {
        transform: rotate(360deg);
      }
    }
  `,
})
export class UiSpinner {
  readonly size = input<SpinnerSize>('medium');
  readonly theme = input<SpinnerTheme>('light');
  readonly ariaLabel = input('Loading');

  protected readonly config = computed(() => {
    const { dimension, strokeWidth, className } = SIZE_CONFIG[this.size()];
    const radius = (dimension - strokeWidth) / 2;
    const circumference = 2 * Math.PI * radius;
    return {
      dimension,
      strokeWidth,
      className,
      radius,
      center: dimension / 2,
      viewBox: `0 0 ${dimension} ${dimension}`,
      dashArray: `${circumference * 0.75} ${circumference}`,
      dashOffset: circumference * 0.25,
      themeClass: THEME_CLASSES[this.theme()],
    };
  });
}
