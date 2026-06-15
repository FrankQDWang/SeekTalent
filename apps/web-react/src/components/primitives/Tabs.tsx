import { useId, type KeyboardEvent, type ReactNode } from "react";
import "./Tabs.css";

export type TabOption<TValue extends string = string> = {
  disabled?: boolean;
  icon?: ReactNode;
  label: ReactNode;
  value: TValue;
};

type TabsProps<TValue extends string = string> = {
  ariaLabel: string;
  className?: string;
  getPanelId?: (value: TValue) => string;
  idPrefix?: string;
  onValueChange: (value: TValue) => void;
  tabClassName?: string;
  tabs: readonly TabOption<TValue>[];
  value: TValue;
};

export function Tabs<TValue extends string = string>({
  ariaLabel,
  className,
  getPanelId,
  idPrefix,
  onValueChange,
  tabClassName,
  tabs,
  value,
}: TabsProps<TValue>) {
  const tabsId = useId();
  const tabIdPrefix = idPrefix ?? tabsId;
  const classes = ["st-tabs", className].filter(Boolean).join(" ");
  const tabClasses = ["st-tabs__tab", tabClassName].filter(Boolean).join(" ");

  function selectTab(
    event: KeyboardEvent<HTMLButtonElement>,
    nextTab: TabOption<TValue> | undefined,
  ) {
    if (!nextTab) {
      return;
    }
    event.preventDefault();
    onValueChange(nextTab.value);
    requestAnimationFrame(() => {
      const button = document.getElementById(tabId(nextTab.value));
      button?.focus();
    });
  }

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, offset: number) {
    const enabledTabs = tabs.filter((tab) => !tab.disabled);
    const activeIndex = enabledTabs.findIndex((tab) => tab.value === value);
    const nextIndex =
      activeIndex === -1
        ? 0
        : (activeIndex + offset + enabledTabs.length) % enabledTabs.length;
    selectTab(event, enabledTabs[nextIndex]);
  }

  function jumpFocus(
    event: KeyboardEvent<HTMLButtonElement>,
    edge: "first" | "last",
  ) {
    const enabledTabs = tabs.filter((tab) => !tab.disabled);
    selectTab(
      event,
      edge === "first" ? enabledTabs[0] : enabledTabs[enabledTabs.length - 1],
    );
  }

  function tabId(tabValue: TValue) {
    return `${tabIdPrefix}-${tabValue}-tab`;
  }

  return (
    <div aria-label={ariaLabel} className={classes} role="tablist">
      {tabs.map((tab) => (
        <button
          aria-selected={value === tab.value}
          aria-controls={getPanelId?.(tab.value)}
          className={tabClasses}
          disabled={tab.disabled}
          id={tabId(tab.value)}
          key={tab.value}
          onClick={() => onValueChange(tab.value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowRight" || event.key === "ArrowDown") {
              moveFocus(event, 1);
            }
            if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
              moveFocus(event, -1);
            }
            if (event.key === "Home") {
              jumpFocus(event, "first");
            }
            if (event.key === "End") {
              jumpFocus(event, "last");
            }
          }}
          role="tab"
          tabIndex={value === tab.value ? 0 : -1}
          type="button"
        >
          {tab.icon}
          <span>{tab.label}</span>
        </button>
      ))}
    </div>
  );
}
