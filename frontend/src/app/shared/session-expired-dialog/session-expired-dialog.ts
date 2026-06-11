import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  inject,
  OnDestroy,
  signal,
  viewChild,
} from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';

const COUNTDOWN_SECONDS = 10;

@Component({
  selector: 'app-session-expired-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule],
  templateUrl: './session-expired-dialog.html',
  styleUrl: './session-expired-dialog.scss',
})
export class SessionExpiredDialog implements AfterViewInit, OnDestroy {
  private readonly document = inject(DOCUMENT);
  private readonly dialogRef = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  protected readonly countdown = signal(COUNTDOWN_SECONDS);
  protected readonly progressPercent = signal(100);

  private intervalId: ReturnType<typeof setInterval> | null = null;

  ngAfterViewInit(): void {
    this.dialogRef().nativeElement.showModal();
    this.startCountdown();
  }

  ngOnDestroy(): void {
    if (this.intervalId !== null) {
      clearInterval(this.intervalId);
    }
  }

  protected reconnectNow(): void {
    if (this.intervalId !== null) {
      clearInterval(this.intervalId);
    }
    this.document.defaultView?.location.reload();
  }

  protected onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      event.preventDefault();
    }
  }

  private startCountdown(): void {
    this.intervalId = setInterval(() => {
      const next = this.countdown() - 1;
      if (next <= 0) {
        this.countdown.set(0);
        this.progressPercent.set(0);
        if (this.intervalId !== null) {
          clearInterval(this.intervalId);
        }
        this.document.defaultView?.location.reload();
      } else {
        this.countdown.set(next);
        this.progressPercent.set((next / COUNTDOWN_SECONDS) * 100);
      }
    }, 1000);
  }
}
