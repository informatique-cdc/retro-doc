import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { ChatService } from './chat.service';
import { ChatStreamEvent } from './api.models';
import { UserService } from '../auth';

/** A response body we can feed one network chunk at a time. */
function openStream(): {
  body: ReadableStream<Uint8Array>;
  push: (chunk: string) => Promise<void>;
  close: () => Promise<void>;
} {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start: (c) => {
      controller = c;
    },
  });

  // Yield to the event loop so the service's read loop drains what was just
  // enqueued: without it the assertions would race the reader.
  const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

  return {
    body,
    push: async (chunk: string) => {
      controller.enqueue(encoder.encode(chunk));
      await settle();
    },
    close: async () => {
      controller.close();
      await settle();
    },
  };
}

describe('ChatService streaming', () => {
  let service: ChatService;
  let originalFetch: typeof globalThis.fetch;
  let stream: ReturnType<typeof openStream>;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: UserService,
          useValue: { getValidAccessToken: () => Promise.resolve('test-token') },
        },
      ],
    });
    service = TestBed.inject(ChatService);

    stream = openStream();
    originalFetch = globalThis.fetch;
    globalThis.fetch = (() =>
      Promise.resolve({ ok: true, status: 200, body: stream.body } as unknown as Response)) as typeof globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  /** Subscribe and collect everything the stream produces. */
  function collect(): { events: ChatStreamEvent[]; completed: () => boolean; error: () => unknown } {
    const events: ChatStreamEvent[] = [];
    let done = false;
    let failure: unknown = undefined;

    service.resumeChat('abc', 'hello').subscribe({
      next: (event) => events.push(event),
      error: (err) => (failure = err),
      complete: () => (done = true),
    });

    return { events, completed: () => done, error: () => failure };
  }

  it('emits each token as its chunk arrives, not once the stream ends', async () => {
    const collected = collect();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await stream.push('data: {"token": "Hel"}\n\n');
    expect(collected.events).toEqual([{ type: 'token', content: 'Hel' }]);
    expect(collected.completed()).toBe(false);

    await stream.push('data: {"token": "lo"}\n\n');
    expect(collected.events).toEqual([
      { type: 'token', content: 'Hel' },
      { type: 'token', content: 'lo' },
    ]);
    expect(collected.completed()).toBe(false);

    await stream.close();
    expect(collected.completed()).toBe(true);
  });

  it('holds back an event split across chunks until it is whole', async () => {
    const collected = collect();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await stream.push('data: {"tok');
    expect(collected.events).toEqual([]);

    await stream.push('en": "wor');
    expect(collected.events).toEqual([]);

    await stream.push('ld"}\n\n');
    expect(collected.events).toEqual([{ type: 'token', content: 'world' }]);
  });

  it('delivers several events packed into one chunk', async () => {
    const collected = collect();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await stream.push('data: {"token": "a"}\n\ndata: {"token": "b"}\n\ndata: {"token": "c"}\n\n');

    expect(collected.events).toEqual([
      { type: 'token', content: 'a' },
      { type: 'token', content: 'b' },
      { type: 'token', content: 'c' },
    ]);
  });

  it('ignores keep-alive comments', async () => {
    const collected = collect();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await stream.push(': ping\n\n');
    expect(collected.events).toEqual([]);

    await stream.push('data: {"token": "hi"}\n\n');
    expect(collected.events).toEqual([{ type: 'token', content: 'hi' }]);
  });

  it('dispatches named events instead of folding them into the answer', async () => {
    const collected = collect();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await stream.push('event: chat_id\ndata: {"chat_id": "thread-1"}\n\n');
    await stream.push('data: {"token": "Done."}\n\n');
    await stream.push(
      'event: message_saved\ndata: {"message_id": "m2", "human_message_id": "m1", "variant_index": 1, "variant_count": 1}\n\n'
    );
    await stream.push('event: title\ndata: {"title": "A short title"}\n\n');
    await stream.push('event: done\ndata: [DONE]\n\n');

    expect(collected.events).toEqual([
      { type: 'chat_id', chatId: 'thread-1' },
      { type: 'token', content: 'Done.' },
      {
        type: 'message_saved',
        messageId: 'm2',
        humanMessageId: 'm1',
        variantIndex: 1,
        variantCount: 1,
      },
      { type: 'title', title: 'A short title' },
    ]);

    // The rendered answer is the concatenation of the tokens: no event
    // payload may leak into it.
    const answer = collected.events
      .filter((event) => event.type === 'token')
      .map((event) => event.content)
      .join('');
    expect(answer).toBe('Done.');
  });

  it('reports tool lifecycle events with their status and sources', async () => {
    const collected = collect();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await stream.push('event: tool_start\ndata: {"tool": "repo_glob", "id": "t1"}\n\n');
    await stream.push(
      'event: tool_end\ndata: {"tool": "repo_glob", "id": "t1", "status": "success", "sources": [{"path": "a.py", "file_id": "f1"}]}\n\n'
    );

    expect(collected.events).toEqual([
      { type: 'tool_start', tool: 'repo_glob', id: 't1' },
      {
        type: 'tool_end',
        tool: 'repo_glob',
        id: 't1',
        status: 'success',
        sources: [{ path: 'a.py', file_id: 'f1' }],
      },
    ]);
  });

  it('surfaces a server error as an error event rather than as text', async () => {
    const collected = collect();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await stream.push('event: error\ndata: {"error": "Failed to create chat thread."}\n\n');

    expect(collected.events).toEqual([
      { type: 'error', detail: 'Failed to create chat thread.' },
    ]);
  });

  it('parses events terminated with CRLF', async () => {
    const collected = collect();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await stream.push('event: title\r\ndata: {"title": "CRLF"}\r\n\r\n');

    expect(collected.events).toEqual([{ type: 'title', title: 'CRLF' }]);
  });
});
