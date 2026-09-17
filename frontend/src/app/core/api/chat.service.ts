import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { map, Observable, Subscriber } from 'rxjs';
import {
  ChatSource,
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

  getThreads(repoId?: string): Observable<ChatThread[]> {
    let params = new HttpParams();
    if (repoId) {
      params = params.set('repo_id', repoId);
    }
    return this.http
      .get<ChatThreadListResponse>('/api/v0/chat', { params })
      .pipe(map((res) => res.threads));
  }

  /**
   * Fetch one page of a thread's history, newest first.
   *
   * Omitting `before` returns the most recent messages; pass the previous
   * response's `next_cursor` as `before` to load the preceding page. The
   * cursor is absent once the history is exhausted.
   */
  getMessages(
    chatId: string,
    opts?: { limit?: number; before?: string }
  ): Observable<ChatThreadMessagesResponse> {
    let params = new HttpParams();
    if (opts?.limit != null) {
      params = params.set('limit', opts.limit);
    }
    if (opts?.before) {
      params = params.set('before', opts.before);
    }
    return this.http.get<ChatThreadMessagesResponse>(
      `/api/v0/chat/${encodeURIComponent(chatId)}`,
      { params }
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

  /**
   * Answer a question again, keeping the answer it already had.
   *
   * Only the newest answer of a thread can be regenerated; the server
   * rejects anything else before the stream opens.
   */
  retryMessage(chatId: string, messageId: string): Observable<ChatStreamEvent> {
    return this.streamSSE(`/api/v0/chat/${encodeURIComponent(chatId)}/retry`, {
      message_id: messageId,
    });
  }

  /**
   * Switch the conversation onto another answer to the same question.
   *
   * Returns the newest page as the thread now reads: the turns that followed
   * the other answer are hidden, and the ones that followed this one return.
   */
  selectVariant(
    chatId: string,
    messageId: string,
    opts?: { limit?: number }
  ): Observable<ChatThreadMessagesResponse> {
    let params = new HttpParams();
    if (opts?.limit != null) {
      params = params.set('limit', opts.limit);
    }
    return this.http.post<ChatThreadMessagesResponse>(
      `/api/v0/chat/${encodeURIComponent(chatId)}/variant`,
      { message_id: messageId },
      { params }
    );
  }

  private streamSSE(url: string, body: object): Observable<ChatStreamEvent> {
    return new Observable<ChatStreamEvent>((subscriber) => {
      const controller = new AbortController();
      void this.pump(url, body, controller.signal, subscriber);
      return () => controller.abort();
    });
  }

  /**
   * Read an SSE response and emit each event the moment it is decoded.
   *
   * Nothing is accumulated beyond the bytes of an event that has not fully
   * arrived yet, so a token reaches the UI on the network chunk that carries
   * it rather than at the end of the response.
   */
  private async pump(
    url: string,
    body: object,
    signal: AbortSignal,
    subscriber: Subscriber<ChatStreamEvent>
  ): Promise<void> {
    try {
      const token = await this.userService.getValidAccessToken();
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        // Marks the response as a stream for proxies that decide whether to
        // buffer from the request, and keeps any cache out of the path.
        Accept: 'text/event-stream',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal,
        cache: 'no-store',
      });

      if (!response.ok || !response.body) {
        throw new Error(`Chat request failed: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;

        // `stream: true` holds back a multi-byte character split across two
        // network chunks instead of decoding it as a replacement character.
        // Line endings are normalized because the SSE grammar accepts CR,
        // LF and CRLF, and a proxy may not use the server's choice.
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n|\r/g, '\n');

        // Events are separated by a blank line. Whatever follows the last
        // separator is a partial event and waits for the next chunk.
        let boundary = buffer.indexOf('\n\n');
        while (boundary !== -1) {
          this.emitEvent(buffer.slice(0, boundary), subscriber);
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf('\n\n');
        }
      }

      subscriber.complete();
    } catch (err) {
      if ((err as Error | undefined)?.name !== 'AbortError') {
        subscriber.error(err);
      }
    }
  }

  /**
   * Decode one SSE event and forward it to the subscriber.
   *
   * An event is a block of `field: value` lines. Lines opening with `:` are
   * comments — the server sends them as keep-alive pings — and a single
   * space after the colon belongs to the delimiter, not to the value.
   * Multiple `data` lines are one payload split across newlines.
   */
  private emitEvent(raw: string, subscriber: Subscriber<ChatStreamEvent>): void {
    let eventType = '';
    const data: string[] = [];

    for (const line of raw.split('\n')) {
      if (!line || line.startsWith(':')) continue;

      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      const rest = colon === -1 ? '' : line.slice(colon + 1);
      const value = rest.startsWith(' ') ? rest.slice(1) : rest;

      if (field === 'event') {
        eventType = value;
      } else if (field === 'data') {
        data.push(value);
      }
    }

    if (data.length === 0) return;

    const event = this.toStreamEvent(eventType, data.join('\n'));
    if (event) {
      subscriber.next(event);
    }
  }

  /**
   * Map an SSE event name and payload onto a typed stream event.
   *
   * Dispatch is on the event name, so an event the client does not model is
   * dropped rather than mistaken for a token — which would splice its JSON
   * payload into the message the user is reading.
   */
  private toStreamEvent(eventType: string, raw: string): ChatStreamEvent | null {
    // The stream ends when the server closes the body; the sentinel carries
    // no payload of its own.
    if (eventType === 'done' || raw === '[DONE]') return null;

    const payload = this.parsePayload(raw);
    const data = this.asRecord(payload);
    const text = typeof payload === 'string' ? payload : undefined;

    switch (eventType) {
      case 'chat_id': {
        const chatId = text ?? this.str(data, 'chat_id') ?? this.str(data, 'id');
        return chatId ? { type: 'chat_id', chatId } : null;
      }
      case 'title': {
        const title = text ?? this.str(data, 'title');
        return title ? { type: 'title', title } : null;
      }
      case 'tool_start': {
        const tool = text ?? this.str(data, 'tool') ?? '';
        return { type: 'tool_start', tool, id: this.str(data, 'id') ?? '' };
      }
      case 'tool_end': {
        const tool = text ?? this.str(data, 'tool') ?? '';
        const status: ToolStatus = data['status'] === 'error' ? 'error' : 'success';
        return {
          type: 'tool_end',
          tool,
          id: this.str(data, 'id') ?? '',
          status,
          sources: this.sources(data['sources']),
        };
      }
      case 'message_saved': {
        const messageId = this.str(data, 'message_id');
        return messageId
          ? {
              type: 'message_saved',
              messageId,
              humanMessageId: this.str(data, 'human_message_id'),
              // Absent until a question has been answered more than once.
              variantIndex: this.num(data, 'variant_index') ?? 1,
              variantCount: this.num(data, 'variant_count') ?? 1,
              prevVariantId: this.str(data, 'prev_variant_id'),
            }
          : null;
      }
      case 'error': {
        return { type: 'error', detail: text ?? this.str(data, 'error') ?? '' };
      }
      default: {
        // Unnamed events are the token stream.
        const content = text ?? this.str(data, 'token') ?? this.str(data, 'content');
        return content === undefined ? null : { type: 'token', content };
      }
    }
  }

  /** Decode a `data` payload, keeping it as-is when it is not JSON. */
  private parsePayload(raw: string): unknown {
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }

  private asRecord(payload: unknown): Record<string, unknown> {
    return payload !== null && typeof payload === 'object'
      ? (payload as Record<string, unknown>)
      : {};
  }

  private str(data: Record<string, unknown>, key: string): string | undefined {
    const value = data[key];
    return typeof value === 'string' ? value : undefined;
  }

  private num(data: Record<string, unknown>, key: string): number | undefined {
    const value = data[key];
    return typeof value === 'number' ? value : undefined;
  }

  private sources(value: unknown): ChatSource[] | undefined {
    if (!Array.isArray(value)) return undefined;
    const sources = value
      .map((entry) => this.asRecord(entry))
      .filter((entry) => typeof entry['path'] === 'string')
      .map((entry) => ({
        path: entry['path'] as string,
        file_id: this.str(entry, 'file_id') ?? '',
      }));
    return sources.length > 0 ? sources : undefined;
  }
}
