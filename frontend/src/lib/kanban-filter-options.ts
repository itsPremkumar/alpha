/**
 * Keep filter choices readable while making equal display names selectable.
 * Identity remains the option value; the short id is presentation only.
 */
export function uniqueFilterLabels(items: readonly { id: string; name: string }[]): Map<string, string> {
  const byName = new Map<string, Array<{ id: string; index: number }>>();
  items.forEach((item, index) => {
    const group = byName.get(item.name) ?? [];
    group.push({ id: item.id, index });
    byName.set(item.name, group);
  });

  const labels = new Map<string, string>();
  for (const [name, group] of byName) {
    if (group.length === 1) {
      labels.set(group[0].id, name);
      continue;
    }
    for (const item of group) {
      let prefixLength = Math.min(6, item.id.length);
      let suffix = item.id.slice(0, prefixLength);
      while (
        group.some((other) => other.index !== item.index && other.id.startsWith(suffix)) &&
        prefixLength < item.id.length
      ) {
        prefixLength += 1;
        suffix = item.id.slice(0, prefixLength);
      }
      if (group.some((other) => other.index !== item.index && other.id === item.id)) {
        suffix = `${item.id} #${item.index + 1}`;
      }
      labels.set(item.id, `${name} (${suffix})`);
    }
  }
  return labels;
}
