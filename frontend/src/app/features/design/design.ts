import { ChangeDetectionStrategy, Component } from '@angular/core';
import { UiButton } from '@design-system';

@Component({
  selector: 'app-design',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [UiButton],
  templateUrl: './design.html',
  styleUrl: './design.scss',
})
export class Design {}
