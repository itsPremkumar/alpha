type CardKeyEvent = {
  key: string;
  repeat?: boolean;
  preventDefault: () => void;
};

type SpacePress = { current: string | null };

/** Match the expected button keyboard contract for a non-native card element. */
export function handleKanbanCardKeyDown(
  event: CardKeyEvent,
  cardId: string,
  spacePress: SpacePress,
  activate: () => void,
): void {
  if (event.key === " ") {
    event.preventDefault();
    if (!event.repeat) spacePress.current = cardId;
    return;
  }

  if (event.key === "Enter" && !event.repeat) activate();
}

/** Space activates on keyup, as it does for a native button. */
export function handleKanbanCardKeyUp(
  event: CardKeyEvent,
  cardId: string,
  spacePress: SpacePress,
  activate: () => void,
): void {
  if (event.key !== " ") return;
  event.preventDefault();
  if (spacePress.current !== cardId) return;

  spacePress.current = null;
  activate();
}
