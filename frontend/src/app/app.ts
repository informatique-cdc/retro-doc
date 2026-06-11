import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { TranslateModule } from '@ngx-translate/core';
import { Navbar } from './shared/navbar/navbar';
import { AppHeader } from './shared/app-header/app-header';
import { SessionExpiredDialog } from './shared/session-expired-dialog/session-expired-dialog';
import { UserService } from './core/auth';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, TranslateModule, Navbar, AppHeader, SessionExpiredDialog],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  protected readonly userService = inject(UserService);
}
