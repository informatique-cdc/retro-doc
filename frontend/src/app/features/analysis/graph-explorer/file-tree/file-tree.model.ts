import { RepoFile } from '../../../../core/api';

export interface FileTreeNode {
  id: string;
  name: string;
  type: 'folder' | 'file';
  fileId?: string;
  fullPath?: string;
  children?: FileTreeNode[];
}

export function buildFileTree(files: RepoFile[]): FileTreeNode[] {
  if (files.length === 0) return [];

  // Split each path into segments
  const parsed = files.map((f) => ({
    file: f,
    parts: f.path.split('/'),
  }));

  // Find the longest common directory prefix
  let commonLength = 0;
  if (parsed.length > 1) {
    const first = parsed[0].parts;
    const maxLen = Math.min(...parsed.map((p) => p.parts.length)) - 1; // -1 to exclude filename
    for (let i = 0; i < maxLen; i++) {
      if (parsed.every((p) => p.parts[i] === first[i])) {
        commonLength = i + 1;
      } else {
        break;
      }
    }
  }

  // Build a nested map structure
  const root = new Map<string, unknown>();

  for (const { file, parts } of parsed) {
    const relativeParts = parts.slice(commonLength);
    let current = root;

    for (let i = 0; i < relativeParts.length - 1; i++) {
      const segment = relativeParts[i];
      if (!current.has(segment)) {
        current.set(segment, new Map());
      }
      current = current.get(segment) as Map<string, unknown>;
    }

    // Leaf file entry
    const fileName = relativeParts[relativeParts.length - 1];
    current.set(fileName, file);
  }

  // Convert the map to FileTreeNode[], collapsing single-child folder chains
  let idCounter = 0;

  function mapToNodes(map: Map<string, unknown>, pathPrefix: string): FileTreeNode[] {
    const folders: FileTreeNode[] = [];
    const fileNodes: FileTreeNode[] = [];

    for (const [name, value] of map) {
      const currentPath = pathPrefix ? `${pathPrefix}/${name}` : name;

      if (value instanceof Map) {
        // Collapse single-child folder chains: if this folder has exactly one
        // entry and that entry is also a folder, merge them (e.g. "src/app/services")
        let collapsedName = name;
        let collapsedPath = currentPath;
        let innerMap = value as Map<string, unknown>;

        while (innerMap.size === 1) {
          const [onlyKey, onlyValue] = innerMap.entries().next().value as [string, unknown];
          if (!(onlyValue instanceof Map)) break;
          collapsedName = `${collapsedName}/${onlyKey}`;
          collapsedPath = collapsedPath ? `${collapsedPath}/${onlyKey}` : onlyKey;
          innerMap = onlyValue as Map<string, unknown>;
        }

        folders.push({
          id: `folder-${idCounter++}`,
          name: collapsedName,
          type: 'folder',
          fullPath: collapsedPath,
          children: mapToNodes(innerMap, collapsedPath),
        });
      } else {
        const file = value as RepoFile;
        fileNodes.push({
          id: `file-${idCounter++}`,
          name,
          type: 'file',
          fileId: file.file_id,
          fullPath: file.path,
        });
      }
    }

    // Sort folders before files, both alphabetically
    folders.sort((a, b) => a.name.localeCompare(b.name));
    fileNodes.sort((a, b) => a.name.localeCompare(b.name));

    return [...folders, ...fileNodes];
  }

  return mapToNodes(root, '');
}
