export interface ProjectOptionIdentity {
  id: string;
  name: string;
}

/** Keep duplicate project names selectable without changing their stored names. */
export function projectOptionLabel(
  project: ProjectOptionIdentity,
  projects: readonly ProjectOptionIdentity[],
): string {
  const name = project.name.trim() || project.id;
  const normalizedName = name.toLocaleLowerCase();
  const collisions = projects.filter(
    (candidate) => candidate.name.trim().toLocaleLowerCase() === normalizedName,
  );
  if (collisions.length < 2) return name;

  let idLength = Math.min(8, project.id.length);
  while (
    idLength < project.id.length &&
    collisions.some(
      (candidate) =>
        candidate.id !== project.id &&
        candidate.id.slice(0, idLength) === project.id.slice(0, idLength),
    )
  ) {
    idLength = Math.min(project.id.length, idLength + 2);
  }
  return `${name} · ${project.id.slice(0, idLength)}`;
}
