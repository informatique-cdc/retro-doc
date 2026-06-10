import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';
import { FileTreeNode } from './file-tree.model';

@Component({
  selector: 'app-file-tree',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule, NgTemplateOutlet],
  templateUrl: './file-tree.html',
  styleUrl: './file-tree.scss',
})
export class FileTree {
  readonly tree = input.required<FileTreeNode[]>();
  readonly selectedFileId = input<string | null>(null);
  readonly fileSelected = output<string>();

  protected readonly searchQuery = signal('');
  protected readonly expandedIds = signal(new Set<string>());

  protected readonly filteredTree = computed(() => {
    const query = this.searchQuery().toLowerCase().trim();
    const tree = this.tree();
    if (!query) return tree;
    return this.filterNodes(tree, query);
  });

  protected readonly effectiveExpandedIds = computed(() => {
    const query = this.searchQuery().toLowerCase().trim();
    if (query) {
      // Auto-expand all visible folders when searching
      return this.collectFolderIds(this.filteredTree());
    }
    return this.expandedIds();
  });

  protected onSearchInput(value: string): void {
    this.searchQuery.set(value);
  }

  protected toggleFolder(node: FileTreeNode): void {
    this.expandedIds.update((ids) => {
      const next = new Set(ids);
      if (next.has(node.id)) {
        next.delete(node.id);
      } else {
        next.add(node.id);
      }
      return next;
    });
  }

  protected selectFile(node: FileTreeNode): void {
    if (node.fileId) {
      this.fileSelected.emit(node.fileId);
    }
  }

  protected onKeydown(event: KeyboardEvent, node: FileTreeNode): void {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (node.type === 'folder') {
        this.toggleFolder(node);
      } else {
        this.selectFile(node);
      }
    }
  }

  private filterNodes(nodes: FileTreeNode[], query: string): FileTreeNode[] {
    const result: FileTreeNode[] = [];

    for (const node of nodes) {
      if (node.type === 'file') {
        if (node.name.toLowerCase().includes(query)) {
          result.push(node);
        }
      } else if (node.children) {
        const filteredChildren = this.filterNodes(node.children, query);
        if (filteredChildren.length > 0) {
          result.push({ ...node, children: filteredChildren });
        }
      }
    }

    return result;
  }

  private collectFolderIds(nodes: FileTreeNode[]): Set<string> {
    const ids = new Set<string>();
    for (const node of nodes) {
      if (node.type === 'folder') {
        ids.add(node.id);
        if (node.children) {
          for (const id of this.collectFolderIds(node.children)) {
            ids.add(id);
          }
        }
      }
    }
    return ids;
  }
}
