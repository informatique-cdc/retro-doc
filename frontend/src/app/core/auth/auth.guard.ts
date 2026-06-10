import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { UserService } from './user.service';

export function hasRole(...roles: string[]): CanActivateFn {
  return () => {
    const userService = inject(UserService);
    const router = inject(Router);

    if (!userService.isAuthenticated()) {
      return router.parseUrl('/forbidden');
    }

    const hasRequiredRole = roles.some((role) => userService.hasRole(role));
    return hasRequiredRole || router.parseUrl('/forbidden');
  };
}
