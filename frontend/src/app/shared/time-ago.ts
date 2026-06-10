import { TranslateService } from '@ngx-translate/core';

const TIME_UNITS: [number, string][] = [
  [60, 'second'],
  [60, 'minute'],
  [24, 'hour'],
  [7, 'day'],
  [4.345, 'week'],
  [12, 'month'],
  [Number.POSITIVE_INFINITY, 'year'],
];

const UNIT_KEYS: Record<string, string> = {
  second: 'timeAgo.secondsAgo',
  minute: 'timeAgo.minutesAgo',
  hour: 'timeAgo.hoursAgo',
  day: 'timeAgo.daysAgo',
  week: 'timeAgo.weeksAgo',
  month: 'timeAgo.monthsAgo',
  year: 'timeAgo.yearsAgo',
};

const UNIT_PLURAL_KEYS: Record<string, string> = {
  second: 'timeAgo.secondsAgo_plural',
  minute: 'timeAgo.minutesAgo_plural',
  hour: 'timeAgo.hoursAgo_plural',
  day: 'timeAgo.daysAgo_plural',
  week: 'timeAgo.weeksAgo_plural',
  month: 'timeAgo.monthsAgo_plural',
  year: 'timeAgo.yearsAgo_plural',
};

export function timeAgo(isoDate: string, translate: TranslateService): string {
  const now = Date.now();
  const normalized = /[Z+-]/.test(isoDate.slice(-6)) ? isoDate : isoDate + 'Z';
  const then = new Date(normalized).getTime();
  let seconds = Math.floor((now - then) / 1000);
  if (seconds < 5) return translate.instant('timeAgo.justNow');

  for (const [divisor, unit] of TIME_UNITS) {
    if (seconds < divisor) {
      const value = Math.floor(seconds);
      const key = value !== 1 ? UNIT_PLURAL_KEYS[unit] : UNIT_KEYS[unit];
      return translate.instant(key, { value });
    }
    seconds /= divisor;
  }

  return '';
}
