import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable, NgZone } from '@angular/core';
import { map, Observable } from 'rxjs';
import {
  ChatStreamEvent,
  ChatThread,
  ChatThreadListResponse,
  ChatThreadMessagesResponse,
  ToolStatus,
} from './api.models';
import { UserService } from '../auth';

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly http = inject(HttpClient);
  private readonly userService = inject(UserService);
  private readonly zone = inject(NgZone);

  getThreads(repoId?: string): Observable<ChatThread[]> {
    let params = new HttpParams();
    if (repoId) {
      params = params.set('repo_id', repoId);
    }
    return this.http
      .get<ChatThreadListResponse>('/api/v0/chat', { params })
      .pipe(map((res) => res.threads));
  }

  getMessages(chatId: string): Observable<ChatThreadMessagesResponse> {
    return this.http.get<ChatThreadMessagesResponse>(
      `/api/v0/chat/${encodeURIComponent(chatId)}`
    );
  }

  deleteThread(chatId: string): Observable<void> {
    return this.http.delete<void>(`/api/v0/chat/${encodeURIComponent(chatId)}`);
  }

  createChat(repoId: string, message: string): Observable<ChatStreamEvent> {
    return this.streamSSE('/api/v0/chat', { repo_id: repoId, message });
  }

  resumeChat(chatId: string, message: string): Observable<ChatStreamEvent> {
    return this.streamSSE(`/api/v0/chat/${encodeURIComponent(chatId)}`, { message });
  }

  private streamSSE(url: string, body: object): Observable<ChatStreamEvent> {
    return new Observable<ChatStreamEvent>((subscriber) => {
      const controller = new AbortController();

      const token = this.userService.user()?.accessToken;
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error(`Chat request failed: ${response.status}`);
          }

          const reader = response.body?.getReader();
          if (!reader) {
            throw new Error('No response body');
          }

          const decoder = new TextDecoder();
          let buffer = '';
          let currentEvent = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() ?? '';

            for (const line of lines) {
              const trimmed = line.trim();
              if (trimmed.startsWith('event:')) {
                currentEvent = trimmed.slice(6).trim();
              } else if (trimmed.startsWith('data:')) {
                const raw = trimmed.slice(5).trim();
                if (raw === '[DONE]') continue;
                const event = this.parseSSEData(raw, currentEvent);
                if (event) {
                  this.zone.run(() => subscriber.next(event));
                }
                currentEvent = '';
              }
            }
          }

          if (buffer.trim().startsWith('data:')) {
            const raw = buffer.trim().slice(5).trim();
            if (raw !== '[DONE]') {
              const event = this.parseSSEData(raw, currentEvent);
              if (event) {
                this.zone.run(() => subscriber.next(event));
              }
            }
          }

          this.zone.run(() => subscriber.complete());
        })
        .catch((err) => {
          if (err.name !== 'AbortError') {
            this.zone.run(() => subscriber.error(err));
          }
        });

      return () => controller.abort();
    });
  }

  private parseSSEData(raw: string, eventType: string): ChatStreamEvent | null {
    if (eventType === 'chat_id') {
      try {
        const parsed = JSON.parse(raw);
        const chatId = typeof parsed === 'string' ? parsed : parsed.chat_id ?? parsed.id;
        if (chatId) {
          return { type: 'chat_id', chatId };
        }
      } catch {
        return { type: 'chat_id', chatId: raw };
      }
      return null;
    }

    if (eventType === 'tool_start') {
      try {
        const parsed = JSON.parse(raw);
        const tool = typeof parsed === 'string' ? parsed : parsed.tool ?? '';
        const id: string = typeof parsed === 'object' ? parsed.id ?? '' : '';
        return { type: 'tool_start', tool, id };
      } catch {
        return { type: 'tool_start', tool: raw, id: '' };
      }
    }

    if (eventType === 'tool_end') {
      try {
        const parsed = JSON.parse(raw);
        const tool = typeof parsed === 'string' ? parsed : parsed.tool ?? '';
        const id: string = typeof parsed === 'object' ? parsed.id ?? '' : '';
        const status: ToolStatus =
          typeof parsed === 'object' && (parsed.status === 'success' || parsed.status === 'error')
            ? parsed.status
            : 'success';
        return { type: 'tool_end', tool, id, status };
      } catch {
        return { type: 'tool_end', tool: raw, id: '', status: 'success' };
      }
    }

    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed === 'string') {
        return { type: 'token', content: parsed };
      }
      if (parsed && typeof parsed === 'object') {
        const content = parsed.token ?? parsed.content ?? parsed.delta ?? parsed.text ?? raw;
        return { type: 'token', content };
      }
      return { type: 'token', content: raw };
    } catch {
      return { type: 'token', content: raw };
    }
  }
}
