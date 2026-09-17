import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { provideLocationMocks } from '@angular/common/testing';
import { provideTranslateService } from '@ngx-translate/core';
import { Observable, Subject, of, throwError } from 'rxjs';
import { Analysis } from './analysis';
import {
  ChatMessage,
  ChatService,
  ChatStreamEvent,
  ChatThreadMessagesResponse,
  DeepAnalysisService,
  RepoStore,
} from '../../core/api';

/**
 * The parts of `Analysis` these tests drive.
 *
 * They are `protected` or `private` on the component, which is right for the
 * template but leaves nothing for a test to call — so the instance is read
 * through this shape rather than through `any`, which keeps the calls
 * type-checked.
 */
interface AnalysisInternals {
  chatMessages: { (): ChatMessage[]; set(value: ChatMessage[]): void };
  activeChatId: { set(value: string | null): void };
  messagesCursor: { set(value: string | null): void };
  awaitingInitialScroll: boolean;
  isRetrying(): boolean;
  isStreaming(): boolean;
  retryFailed(): boolean;
  hasMoreMessages(): boolean;
  isSwitchingVariant(): boolean;
  retry(msg: ChatMessage): void;
  switchVariant(messageId: string | undefined): void;
  loadOlderMessages(): void;
}

class ChatServiceStub {
  readonly retryStream = new Subject<ChatStreamEvent>();
  readonly variantResponse = new Subject<ChatThreadMessagesResponse>();
  retryCalls: [string, string][] = [];
  variantCalls: string[] = [];

  retryMessage(chatId: string, messageId: string): Observable<ChatStreamEvent> {
    this.retryCalls.push([chatId, messageId]);
    return this.retryStream.asObservable();
  }

  selectVariant(_chatId: string, messageId: string): Observable<ChatThreadMessagesResponse> {
    this.variantCalls.push(messageId);
    return this.variantResponse.asObservable();
  }

  getThreads(): Observable<never[]> {
    return of([]);
  }

  readonly olderPage = new Subject<ChatThreadMessagesResponse>();
  olderPageCalls: string[] = [];

  getMessages(
    _chatId?: string,
    opts?: { limit?: number; before?: string }
  ): Observable<ChatThreadMessagesResponse> {
    // A `before` cursor is what makes it a request for the preceding page.
    if (!opts?.before) {
      return of({ chat_id: 'chat-1', messages: [] });
    }
    this.olderPageCalls.push(opts.before);
    return this.olderPage.asObservable();
  }
}

function answer(): ChatMessage {
  return {
    key: 'local-1',
    id: 'm2',
    role: 'ai',
    content: 'First answer',
    variantIndex: 1,
    variantCount: 1,
  };
}

describe('Analysis regeneration', () => {
  let component: AnalysisInternals;
  let chat: ChatServiceStub;

  beforeEach(() => {
    chat = new ChatServiceStub();

    TestBed.configureTestingModule({
      providers: [
        provideLocationMocks(),
        provideTranslateService({}),
        { provide: ChatService, useValue: chat },
        {
          provide: RepoStore,
          useValue: { getRepo: () => of(null), getRepoFiles: () => of([]) },
        },
        { provide: DeepAnalysisService, useValue: { listAnalyses: () => of([]) } },
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: of(convertToParamMap({ id: 'repo-1' })),
            snapshot: { paramMap: convertToParamMap({ id: 'repo-1' }) },
          },
        },
      ],
    });
    // The real template pulls in the graph explorer, markdown and mermaid;
    // none of it is under test here, and rendering it would only add ways to
    // fail for reasons unrelated to regeneration.
    TestBed.overrideComponent(Analysis, { set: { template: '' } });

    component = TestBed.createComponent(Analysis).componentInstance as unknown as AnalysisInternals;
    component.activeChatId.set('chat-1');
    component.chatMessages.set([
      { key: 'local-0', id: 'm1', role: 'user', content: 'A question' },
      answer(),
    ]);
  });

  it('replaces the answer in place instead of appending a second one', () => {
    component.retry(answer());
    chat.retryStream.next({ type: 'token', content: 'Second answer' });

    const messages = component.chatMessages();
    expect(messages).toHaveLength(2);
    expect(messages[1].key).toBe('local-1');
    expect(messages[1].content).toBe('Second answer');
  });

  it('sends the answer being regenerated to the retry endpoint', () => {
    component.retry(answer());

    expect(chat.retryCalls).toEqual([['chat-1', 'm2']]);
  });

  it('takes the pager position from the saved message', () => {
    component.retry(answer());
    chat.retryStream.next({
      type: 'message_saved',
      messageId: 'm3',
      variantIndex: 2,
      variantCount: 2,
      prevVariantId: 'm2',
    });

    expect(component.chatMessages()[1]).toMatchObject({
      id: 'm3',
      variantIndex: 2,
      variantCount: 2,
      prevVariantId: 'm2',
      nextVariantId: undefined,
    });
  });

  it('puts the previous answer back when the regeneration fails', () => {
    component.retry(answer());
    chat.retryStream.next({ type: 'token', content: 'half a' });
    chat.retryStream.error(new Error('connection lost'));

    expect(component.chatMessages()[1]).toMatchObject({
      content: 'First answer',
      variantCount: 1,
    });
    expect(component.retryFailed()).toBe(true);
    expect(component.isRetrying()).toBe(false);
    expect(component.isStreaming()).toBe(false);
  });

  it('refuses to regenerate a message that was never saved', () => {
    component.retry({ key: 'local-9', role: 'ai', content: 'Still streaming' });

    expect(chat.retryCalls).toEqual([]);
    expect(component.isRetrying()).toBe(false);
  });

  it('refuses to start a second regeneration while one is running', () => {
    component.retry(answer());
    component.retry(answer());

    expect(chat.retryCalls).toHaveLength(1);
  });
});

describe('Analysis variant switching', () => {
  let component: AnalysisInternals;
  let chat: ChatServiceStub;

  beforeEach(() => {
    chat = new ChatServiceStub();

    TestBed.configureTestingModule({
      providers: [
        provideLocationMocks(),
        provideTranslateService({}),
        { provide: ChatService, useValue: chat },
        {
          provide: RepoStore,
          useValue: { getRepo: () => of(null), getRepoFiles: () => of([]) },
        },
        { provide: DeepAnalysisService, useValue: { listAnalyses: () => of([]) } },
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: of(convertToParamMap({ id: 'repo-1' })),
            snapshot: { paramMap: convertToParamMap({ id: 'repo-1' }) },
          },
        },
      ],
    });
    TestBed.overrideComponent(Analysis, { set: { template: '' } });

    component = TestBed.createComponent(Analysis).componentInstance as unknown as AnalysisInternals;
    component.activeChatId.set('chat-1');
    component.chatMessages.set([
      { key: 'm1', id: 'm1', role: 'user', content: 'A question' },
      { key: 'm3', id: 'm3', role: 'ai', content: 'Second answer', variantIndex: 2, variantCount: 2 },
      { key: 'm4', id: 'm4', role: 'user', content: 'A follow-up' },
      { key: 'm5', id: 'm5', role: 'ai', content: 'Its answer' },
    ]);
  });

  it('replaces the conversation with the branch the server returns', () => {
    component.switchVariant('m2');
    chat.variantResponse.next({
      chat_id: 'chat-1',
      messages: [
        { id: 'm1', role: 'human', content: 'A question' },
        {
          id: 'm2',
          role: 'ai',
          content: 'First answer',
          variant_index: 1,
          variant_count: 2,
          next_variant_id: 'm3',
        },
      ],
    });

    const messages = component.chatMessages();
    expect(messages.map((m) => m.id)).toEqual(['m1', 'm2']);
    expect(messages[1]).toMatchObject({ variantIndex: 1, variantCount: 2, nextVariantId: 'm3' });
    expect(component.hasMoreMessages()).toBe(false);
    expect(component.isSwitchingVariant()).toBe(false);
  });

  it('leaves the conversation alone when the switch fails', () => {
    chat.selectVariant = () => throwError(() => new Error('offline'));

    component.switchVariant('m2');

    expect(component.chatMessages().map((m) => m.id)).toEqual(['m1', 'm3', 'm4', 'm5']);
    expect(component.isSwitchingVariant()).toBe(false);
  });

  it('ignores an arrow that has no answer behind it', () => {
    component.switchVariant(undefined);

    expect(chat.variantCalls).toEqual([]);
  });

  it('drops an older page that was still in flight when the branch changed', () => {
    component.messagesCursor.set('m1');
    component.awaitingInitialScroll = false;
    component.loadOlderMessages();
    // Without this the rest would hold for the wrong reason: no request, no
    // response to ignore.
    expect(chat.olderPageCalls).toEqual(['m1']);

    // The user switches answers before that page comes back.
    component.switchVariant('m2');
    chat.variantResponse.next({
      chat_id: 'chat-1',
      messages: [{ id: 'm1', role: 'human', content: 'A question' }],
    });

    chat.olderPage.next({
      chat_id: 'chat-1',
      messages: [{ id: 'old', role: 'ai', content: 'From the other branch' }],
      next_cursor: 'older',
    });

    // Prepending it would splice the abandoned branch into the new one, and
    // its cursor would page further back through that branch's history.
    expect(component.chatMessages().map((m) => m.id)).toEqual(['m1']);
    expect(component.hasMoreMessages()).toBe(false);
  });
});
