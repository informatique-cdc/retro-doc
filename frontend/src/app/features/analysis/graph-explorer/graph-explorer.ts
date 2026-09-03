import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  ElementRef,
  inject,
  input,
  OnDestroy,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { UpperCasePipe } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import cytoscape from 'cytoscape';
import cytoscapeDagre from 'cytoscape-dagre';
import { finalize } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { FileGraphsResponse, RepoFile, RepoService } from '../../../core/api';
import {
  convertAstToElements,
  convertScopedGraphToElements,
  formatScopeLabel,
  GraphType,
  isLegacyGraphData,
} from './graph-explorer.model';
import { FileTree } from './file-tree/file-tree';
import { buildFileTree } from './file-tree/file-tree.model';
import {
  FileContentMode,
  FileSourceViewer,
} from '../../../shared/file-source-viewer/file-source-viewer';

cytoscape.use(cytoscapeDagre);

@Component({
  selector: 'app-graph-explorer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [UpperCasePipe, TranslateModule, FileTree, FileSourceViewer],
  templateUrl: './graph-explorer.html',
  styleUrl: './graph-explorer.scss',
})
export class GraphExplorer implements AfterViewInit, OnDestroy {
  readonly closed = output<void>();
  readonly chatRequested = output<{ nodeLabel: string; fileName: string }>();
  readonly repoId = input.required<string>();
  readonly files = input.required<RepoFile[]>();

  private readonly repoService = inject(RepoService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly translateService = inject(TranslateService);

  private readonly dialogRef = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');
  private readonly cyContainer = viewChild.required<ElementRef<HTMLDivElement>>('cyContainer');

  private cy: cytoscape.Core | null = null;

  protected readonly selectedFileId = signal<string | null>(null);
  protected readonly selectedGraphType = signal<GraphType>('ast');
  protected readonly selectedScope = signal<string | null>(null);
  protected readonly graphData = signal<FileGraphsResponse | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly visibleNodeCount = signal(0);
  protected readonly selectedNodeLabel = signal<string | null>(null);

  protected readonly activePanel = signal<FileContentMode | null>(null);
  protected readonly docAvailable = signal(false);

  protected readonly fileTree = computed(() => buildFileTree(this.files()));

  protected readonly legacyFormat = computed(() => {
    const data = this.graphData();
    return data ? isLegacyGraphData(data) : false;
  });

  protected readonly availableScopes = computed(() => {
    const data = this.graphData();
    const type = this.selectedGraphType();
    if (!data || type === 'ast') return [];
    const graphs = type === 'cfg' ? data.cfg : data.dfg;
    return graphs.map((g) => ({
      value: g.scope ?? '(global)',
      label: formatScopeLabel(g.scope),
    }));
  });

  protected readonly selectedFileName = computed(() => {
    const fileId = this.selectedFileId();
    if (!fileId) return null;
    const file = this.files().find((f) => f.file_id === fileId);
    return file?.path ?? null;
  });

  protected readonly graphDescription = computed(() => {
    const count = this.visibleNodeCount();
    const fileName = this.selectedFileName();
    const type = this.selectedGraphType().toUpperCase();
    if (!fileName) return this.translateService.instant('graphExplorer.noFileSelected');
    return this.translateService.instant('graphExplorer.graphDescription', {
      type,
      fileName,
      count,
    });
  });

  ngAfterViewInit(): void {
    this.dialogRef().nativeElement.showModal();
    this.initCytoscape();
  }

  ngOnDestroy(): void {
    if (this.cy) {
      this.cy.destroy();
      this.cy = null;
    }
  }

  protected close(): void {
    this.dialogRef().nativeElement.close();
  }

  protected onDialogClose(): void {
    this.closed.emit();
  }

  protected onBackdropClick(event: MouseEvent): void {
    if (event.target === this.dialogRef().nativeElement) {
      this.close();
    }
  }

  protected onFileSelect(fileId: string): void {
    if (!fileId) return;
    this.selectedFileId.set(fileId);
    this.activePanel.set(null);
    this.docAvailable.set(false);
    this.loading.set(true);
    this.error.set(null);
    this.graphData.set(null);

    this.repoService
      .getFileGraphs(this.repoId(), fileId)
      .pipe(
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (data) => {
          this.graphData.set(data);
          this.selectedScope.set(null);
          this.renderGraph();
        },
        error: () => {
          this.error.set(this.translateService.instant('graphExplorer.loadFailed'));
        },
      });

    this.repoService
      .getFileDoc(this.repoId(), fileId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (doc) => this.docAvailable.set(doc.content.trim().length > 0),
        error: () => this.docAvailable.set(false),
      });
  }

  protected openSource(): void {
    if (this.selectedFileId()) {
      this.activePanel.set('source');
    }
  }

  protected openDoc(): void {
    if (this.selectedFileId() && this.docAvailable()) {
      this.activePanel.set('documentation');
    }
  }

  protected onPanelClosed(): void {
    this.activePanel.set(null);
  }

  protected onGraphTypeChange(type: GraphType): void {
    this.selectedGraphType.set(type);
    this.selectedScope.set(null);
    this.renderGraph();
  }

  protected onScopeChange(scope: string): void {
    this.selectedScope.set(scope);
    this.renderGraph();
  }

  protected onChatAboutNode(): void {
    const label = this.selectedNodeLabel();
    if (label) {
      this.chatRequested.emit({ nodeLabel: label, fileName: this.selectedFileName() ?? '' });
      this.close();
    }
  }

  private initCytoscape(): void {
    this.cy = cytoscape({
      container: this.cyContainer().nativeElement,
      elements: [],
      style: this.getCytoscapeStyles(),
      layout: { name: 'preset' },
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
      autoungrabify: true,
    });

    this.cy.on('tap', 'node', (event) => {
      this.selectedNodeLabel.set(event.target.data('label') ?? null);
    });

    this.cy.on('tap', (event) => {
      if (event.target === this.cy) {
        this.selectedNodeLabel.set(null);
      }
    });
  }

  private renderGraph(): void {
    if (!this.cy) return;

    this.selectedNodeLabel.set(null);

    const data = this.graphData();
    if (!data) {
      this.cy.elements().remove();
      this.visibleNodeCount.set(0);
      return;
    }

    const type = this.selectedGraphType();
    let elements: { nodes: cytoscape.ElementDefinition[]; edges: cytoscape.ElementDefinition[] };

    if (type === 'ast') {
      elements = data.ast ? convertAstToElements(data.ast) : { nodes: [], edges: [] };
    } else {
      const graphs = type === 'cfg' ? data.cfg : data.dfg;
      const scopeLabel = this.selectedScope();
      const scopeIndex = scopeLabel
        ? graphs.findIndex((g) => (g.scope ?? '(global)') === scopeLabel)
        : 0;
      const graph = graphs[scopeIndex >= 0 ? scopeIndex : 0];
      elements = graph ? convertScopedGraphToElements(graph.content) : { nodes: [], edges: [] };

      if (!scopeLabel && graphs.length > 0) {
        this.selectedScope.set(graphs[0]?.scope ?? '(global)');
      }
    }

    this.cy.elements().remove();
    this.cy.add([...elements.nodes, ...elements.edges]);
    this.cy.layout(this.getLayoutOptions(type)).run();
    this.visibleNodeCount.set(this.cy.nodes().length);
  }

  private getCytoscapeStyles(): cytoscape.StylesheetStyle[] {
    return [
      {
        selector: 'node',
        style: {
          label: 'data(label)',
          shape: 'round-rectangle',
          width: 'label',
          height: 36,
          'padding-left': '16px',
          'padding-right': '16px',
          'background-color': '#eef2ff',
          'border-width': 1.5,
          'border-color': '#c7d2fe',
          color: '#1e293b',
          'font-size': '13px',
          'text-valign': 'center',
          'text-halign': 'center',
          'text-wrap': 'ellipsis',
          'text-max-width': '200px',
        } as cytoscape.Css.Node,
      },
      {
        selector: 'node[nodeType="entry"]',
        style: {
          'background-color': '#dcfce7',
          'border-color': '#86efac',
          shape: 'ellipse',
        } as cytoscape.Css.Node,
      },
      {
        selector: 'node[nodeType="exit"]',
        style: {
          'background-color': '#fee2e2',
          'border-color': '#fca5a5',
          shape: 'ellipse',
        } as cytoscape.Css.Node,
      },
      {
        selector: 'node[nodeType="branch"]',
        style: {
          'background-color': '#fef9c3',
          'border-color': '#fde047',
          shape: 'diamond',
          height: 48,
        } as cytoscape.Css.Node,
      },
      {
        selector: 'node[nodeType="class"], node[nodeType="interface"]',
        style: {
          'background-color': '#dbeafe',
          'border-color': '#93c5fd',
          'font-weight': 'bold',
        } as cytoscape.Css.Node,
      },
      {
        selector: 'node[nodeType="method"]',
        style: {
          'background-color': '#ede9fe',
          'border-color': '#c4b5fd',
        } as cytoscape.Css.Node,
      },
      {
        selector: 'node[nodeType="field"]',
        style: {
          'background-color': '#fce7f3',
          'border-color': '#f9a8d4',
        } as cytoscape.Css.Node,
      },
      {
        selector: 'node[nodeType="statement"]',
        style: {
          'background-color': '#f1f5f9',
          'border-color': '#cbd5e1',
          color: '#475569',
          'font-size': '12px',
        } as cytoscape.Css.Node,
      },
      {
        selector: 'node:selected',
        style: {
          'border-width': 3,
          'border-color': '#4f46e5',
          'overlay-color': '#6366f1',
          'overlay-padding': 6,
          'overlay-opacity': 0.12,
        } as cytoscape.Css.Node,
      },
      {
        selector: 'edge',
        style: {
          width: 1.5,
          'line-color': '#cbd5e1',
          'curve-style': 'bezier',
          'target-arrow-color': '#cbd5e1',
          'target-arrow-shape': 'triangle',
          'arrow-scale': 0.8,
          label: 'data(label)',
          'font-size': '11px',
          color: '#6b7280',
          'text-rotation': 'autorotate',
          'text-margin-y': -10,
        } as cytoscape.Css.Edge,
      },
      {
        selector: 'edge[label="true"]',
        style: {
          'line-color': '#22c55e',
          'target-arrow-color': '#22c55e',
          color: '#16a34a',
        } as cytoscape.Css.Edge,
      },
      {
        selector: 'edge[label="false"]',
        style: {
          'line-color': '#ef4444',
          'target-arrow-color': '#ef4444',
          color: '#dc2626',
        } as cytoscape.Css.Edge,
      },
    ];
  }

  private getLayoutOptions(type: GraphType): cytoscape.LayoutOptions {
    return {
      name: 'dagre',
      rankDir: type === 'ast' ? 'TB' : 'LR',
      rankSep: type === 'ast' ? 60 : 100,
      nodeSep: 20,
      edgeSep: 10,
      animate: true,
      animationDuration: 300,
      fit: true,
      padding: 40,
    } as cytoscape.LayoutOptions;
  }
}
