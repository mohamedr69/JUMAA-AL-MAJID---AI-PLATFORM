import { useEffect } from "react";

const MESSAGE = "You have unsaved changes on this page. Leave without saving them?";

/** Warn before unsaved edits are lost: closing or reloading the tab (the
 * browser's own prompt) and following a link inside the app (a confirm).
 *
 * The app uses a plain BrowserRouter, where React Router's navigation
 * blocker is not available, so in-app links are caught in the capture phase
 * on the document -- before React Router's own click handler sees them. */
export function useUnsavedChanges(dirty: boolean, message: string = MESSAGE): void {
  useEffect(() => {
    if (!dirty) return;

    function beforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      // Chrome still requires returnValue to be set for the prompt to show.
      event.returnValue = message;
    }

    function click(event: MouseEvent) {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      const anchor = (event.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopPropagation();
      }
    }

    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
    };
  }, [dirty, message]);
}
