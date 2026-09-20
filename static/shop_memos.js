const MOBILE_MEMO_MEDIA = window.matchMedia("(max-width: 700px)");
const MOBILE_MEMO_SETTINGS = Object.freeze({
    AUTOSAVE_DELAY_MS: 750,
    LONG_PRESS_MS: 500,
    MOVE_CANCEL_PX: 10,
    DIRECTION_BIAS: 1.2,
    PIN_DISTANCE_RATIO: 0.45,
    DELETE_DISTANCE_RATIO: 0.7,
    FAST_SWIPE_MIN_RATIO: 0.25,
    FAST_SWIPE_VELOCITY: 0.65,
    SCROLL_END_TOLERANCE_PX: 3,
    SNACKBAR_DURATION_MS: 10000,
    COPY_FEEDBACK_DURATION_MS: 2500,
});

const memoSearchInput = document.getElementById("memo-search");
const memoSearchClearButton = document.getElementById("memo-search-clear");
const addMemoButton = document.querySelector(".shop-tools-fab");
const memoCreateDialog = document.getElementById("shop-memo-create-dialog");
const memoCreateCloseButton = document.getElementById("shop-memo-create-close");
const memoBodyInput = document.getElementById("shop-memo-body");
const emptyTrashButton = document.getElementById("shop-memo-empty-trash-open");
const emptyTrashDialog = document.getElementById("shop-memo-empty-trash-dialog");
const mobileConfig = document.getElementById("shop-memo-mobile-config");
const mobileActionSheet = document.getElementById("shop-memo-mobile-actions");
const mobileActionClose = mobileActionSheet?.querySelector(
    ".shop-memo-mobile-actions-close"
);
const mobilePinAction = mobileActionSheet?.querySelector(
    '[data-mobile-action="pin"]'
);
const mobileCopyAction = mobileActionSheet?.querySelector(
    '[data-mobile-action="copy"]'
);
const mobileTrashAction = mobileActionSheet?.querySelector(
    '[data-mobile-action="trash"]'
);
const mobilePreviewTitle = mobileActionSheet?.querySelector(
    "[data-mobile-preview-title]"
);
const mobilePreviewBody = mobileActionSheet?.querySelector(
    "[data-mobile-preview-body]"
);
const memoSnackbar = document.getElementById("shop-memo-snackbar");
const memoSnackbarMessage = document.getElementById(
    "shop-memo-snackbar-message"
);
const memoSnackbarUndo = document.getElementById(
    "shop-memo-snackbar-undo"
);

let snackbarTimer = null;
let snackbarRestoreUrl = null;
let activeMobileMemo = null;

if (memoSearchInput && memoSearchClearButton) {
    const updateClearButtonVisibility = () => {
        memoSearchClearButton.hidden = memoSearchInput.value.length === 0;
    };

    memoSearchInput.addEventListener("input", updateClearButtonVisibility);
    memoSearchClearButton.addEventListener("click", () => {
        const hasAppliedQuery = new URLSearchParams(window.location.search).has("q");
        memoSearchInput.value = "";
        updateClearButtonVisibility();

        if (hasAppliedQuery) {
            window.location.replace(
                `${memoSearchClearButton.dataset.clearUrl}#memo-search`
            );
            return;
        }

        memoSearchInput.focus();
    });

    if (window.location.hash === "#memo-search") {
        memoSearchInput.focus();
    }
}

const parseJsonResponse = async (response) => {
    try {
        return await response.json();
    } catch (_error) {
        return {};
    }
};

const postMemoJson = async (url, payload = {}, options = {}) => {
    if (!url || !mobileConfig?.dataset.csrfToken) {
        throw new Error("保存の準備ができていません。");
    }

    const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": mobileConfig.dataset.csrfToken,
        },
        body: JSON.stringify(payload),
        keepalive: options.keepalive === true,
    });
    const data = await parseJsonResponse(response);

    if (!response.ok || data.ok !== true) {
        throw new Error(data.error || "操作を完了できませんでした。");
    }

    return data;
};

const hideSnackbar = () => {
    if (!memoSnackbar) {
        return;
    }

    memoSnackbar.hidden = true;
    snackbarRestoreUrl = null;
    if (snackbarTimer) {
        window.clearTimeout(snackbarTimer);
        snackbarTimer = null;
    }
};

const showSnackbar = (
    message,
    restoreUrl = null,
    duration = MOBILE_MEMO_SETTINGS.SNACKBAR_DURATION_MS
) => {
    if (!memoSnackbar || !memoSnackbarMessage || !memoSnackbarUndo) {
        return;
    }

    if (snackbarTimer) {
        window.clearTimeout(snackbarTimer);
    }

    memoSnackbarMessage.textContent = message;
    snackbarRestoreUrl = restoreUrl;
    memoSnackbarUndo.hidden = !restoreUrl;
    memoSnackbar.hidden = false;
    snackbarTimer = window.setTimeout(
        hideSnackbar,
        duration
    );
};

memoSnackbarUndo?.addEventListener("click", async () => {
    if (!snackbarRestoreUrl) {
        return;
    }

    memoSnackbarUndo.disabled = true;
    try {
        await postMemoJson(snackbarRestoreUrl);
        window.location.assign(mobileConfig.dataset.listUrl);
    } catch (error) {
        showSnackbar(error.message);
    } finally {
        memoSnackbarUndo.disabled = false;
    }
});

const memoActionMenu = document.getElementById("shop-memo-action-menu");
const memoActionMenuEdit = document.getElementById("shop-memo-menu-edit");
const memoActionMenuTrashForm = document.getElementById(
    "shop-memo-menu-trash-form"
);
const memoMenuButtons = document.querySelectorAll(".shop-memo-menu-button");
const memoCardOpenButtons = document.querySelectorAll(".shop-memo-card-open");
const memoDialogCloseButtons = document.querySelectorAll(
    ".shop-memo-dialog-close"
);

let activeMemoMenuButton = null;

const closeMemoActionMenu = () => {
    if (!memoActionMenu) {
        return;
    }

    memoActionMenu.hidden = true;
    if (activeMemoMenuButton) {
        activeMemoMenuButton.setAttribute("aria-expanded", "false");
    }
    activeMemoMenuButton = null;
};

const editorStates = new WeakMap();

const syncMobileEditorViewportHeight = (dialog) => {
    const scrollContainer = dialog.querySelector(
        ".shop-memo-create-dialog-content, .shop-memo-edit-dialog-content"
    );
    if (!scrollContainer) {
        return;
    }

    if (!MOBILE_MEMO_MEDIA.matches) {
        scrollContainer.style.removeProperty(
            "--shop-memo-mobile-viewport-height"
        );
        return;
    }

    const viewportHeight = window.visualViewport?.height || window.innerHeight;
    scrollContainer.style.setProperty(
        "--shop-memo-mobile-viewport-height",
        `${viewportHeight}px`
    );
};

const resizeMobileEditorTextarea = (dialog) => {
    const textarea = dialog.querySelector('textarea[name="body"]');
    if (!textarea) {
        return;
    }

    if (!MOBILE_MEMO_MEDIA.matches) {
        textarea.style.height = "";
        return;
    }

    textarea.style.height = "auto";
    const minimumHeight = textarea.clientHeight;
    textarea.style.height = `${Math.max(
        minimumHeight,
        textarea.scrollHeight
    )}px`;
};

const updateEditorScrollIndicator = (dialog) => {
    const scrollContainer = dialog.querySelector(
        ".shop-memo-create-dialog-content, .shop-memo-edit-dialog-content"
    );
    const toolbar = dialog.querySelector(
        ".shop-memo-mobile-editor-toolbar"
    );
    if (!scrollContainer || !toolbar) {
        return;
    }

    const remainingScroll = (
        scrollContainer.scrollHeight
        - scrollContainer.clientHeight
        - scrollContainer.scrollTop
    );
    toolbar.dataset.scrollState = (
        remainingScroll > MOBILE_MEMO_SETTINGS.SCROLL_END_TOLERANCE_PX
            ? "more"
            : "end"
    );
};

const scheduleScrollIndicatorUpdate = (dialog) => {
    window.requestAnimationFrame(() => updateEditorScrollIndicator(dialog));
};

const refreshMobileEditorLayout = (dialog) => {
    syncMobileEditorViewportHeight(dialog);
    resizeMobileEditorTextarea(dialog);
    scheduleScrollIndicatorUpdate(dialog);
};

const showAutosaveError = (dialog, message) => {
    const errorBox = dialog.querySelector(".shop-memo-autosave-error");
    const errorMessage = dialog.querySelector("[data-autosave-error-message]");
    if (errorMessage) {
        errorMessage.textContent = message;
    }
    if (errorBox) {
        errorBox.hidden = false;
    }
};

const hideAutosaveError = (dialog) => {
    const errorBox = dialog.querySelector(".shop-memo-autosave-error");
    if (errorBox) {
        errorBox.hidden = true;
    }
};

const applySavedMemoToEditor = (dialog, memo) => {
    dialog.dataset.memoId = String(memo.id);
    dialog.dataset.autosaveUrl = memo.autosave_url;
    dialog.dataset.pinUrl = memo.pin_url;
    dialog.dataset.trashUrl = memo.trash_url;
    dialog.dataset.restoreUrl = memo.restore_url;
    dialog.dataset.pinned = memo.pinned ? "true" : "false";

    const titleInput = dialog.querySelector('input[name="title"]');
    if (titleInput) {
        titleInput.value = memo.title;
    }

    const toolbar = dialog.querySelector(
        ".shop-memo-mobile-editor-toolbar"
    );
    if (toolbar) {
        toolbar.hidden = false;
        scheduleScrollIndicatorUpdate(dialog);
    }
};

const saveMobileEditor = async (dialog, options = {}) => {
    const state = editorStates.get(dialog);
    const textarea = dialog.querySelector('textarea[name="body"]');
    if (!state || !textarea || !state.dirty) {
        return true;
    }

    if (state.saving) {
        const priorSaveSucceeded = await state.saving;
        if (!priorSaveSucceeded || !state.dirty) {
            return priorSaveSucceeded;
        }
    }

    const submittedBody = textarea.value;
    if (!submittedBody.trim()) {
        if (!dialog.dataset.memoId) {
            state.dirty = false;
            hideAutosaveError(dialog);
            return true;
        }
        showAutosaveError(dialog, "メモ本文を空にはできません。");
        return false;
    }

    if (state.timer) {
        window.clearTimeout(state.timer);
        state.timer = null;
    }

    const saveRequest = (async () => {
        try {
            const result = await postMemoJson(
                dialog.dataset.autosaveUrl,
                {body: submittedBody},
                {keepalive: options.keepalive === true}
            );
            applySavedMemoToEditor(dialog, result.memo);
            hideAutosaveError(dialog);
            if (textarea.value === submittedBody) {
                state.dirty = false;
            }
            return true;
        } catch (error) {
            if (!options.silent) {
                showAutosaveError(dialog, error.message);
            }
            return false;
        }
    })();

    state.saving = saveRequest;
    const succeeded = await saveRequest;
    if (state.saving === saveRequest) {
        state.saving = null;
    }

    if (succeeded && state.dirty && textarea.value !== submittedBody) {
        state.timer = window.setTimeout(
            () => saveMobileEditor(dialog),
            MOBILE_MEMO_SETTINGS.AUTOSAVE_DELAY_MS
        );
    }
    return succeeded;
};

const scheduleMobileAutosave = (dialog) => {
    if (!MOBILE_MEMO_MEDIA.matches) {
        return;
    }

    const state = editorStates.get(dialog);
    if (!state) {
        return;
    }

    state.dirty = true;
    hideAutosaveError(dialog);
    if (state.timer) {
        window.clearTimeout(state.timer);
    }
    state.timer = window.setTimeout(
        () => saveMobileEditor(dialog),
        MOBILE_MEMO_SETTINGS.AUTOSAVE_DELAY_MS
    );
};

const returnFromMobileEditor = async (dialog) => {
    const succeeded = await saveMobileEditor(dialog);
    if (succeeded) {
        window.location.assign(mobileConfig.dataset.listUrl);
    }
};

document.querySelectorAll("dialog[data-memo-editor]").forEach((dialog) => {
    const textarea = dialog.querySelector('textarea[name="body"]');
    const scrollContainer = dialog.querySelector(
        ".shop-memo-create-dialog-content, .shop-memo-edit-dialog-content"
    );
    const retryButton = dialog.querySelector(".shop-memo-autosave-retry");
    const backButton = dialog.querySelector(".shop-memo-mobile-editor-back");
    editorStates.set(dialog, {dirty: false, timer: null, saving: null});

    textarea?.addEventListener("input", () => {
        scheduleMobileAutosave(dialog);
        refreshMobileEditorLayout(dialog);
    });
    scrollContainer?.addEventListener(
        "scroll",
        () => updateEditorScrollIndicator(dialog),
        {passive: true}
    );
    retryButton?.addEventListener("click", () => saveMobileEditor(dialog));
    backButton?.addEventListener("click", () => returnFromMobileEditor(dialog));
    dialog.addEventListener("cancel", (event) => {
        if (MOBILE_MEMO_MEDIA.matches) {
            event.preventDefault();
            returnFromMobileEditor(dialog);
        }
    });
});

window.addEventListener("pagehide", () => {
    document.querySelectorAll("dialog[data-memo-editor][open]").forEach(
        (dialog) => saveMobileEditor(
            dialog,
            {keepalive: true, silent: true}
        )
    );
});

const openMemoDialog = (dialog) => {
    if (!dialog) {
        return;
    }

    closeMemoActionMenu();
    if (!dialog.open) {
        dialog.showModal();
    }

    if (MOBILE_MEMO_MEDIA.matches) {
        refreshMobileEditorLayout(dialog);
        dialog.querySelector('textarea[name="body"]')?.focus();
    } else {
        dialog.querySelector('input[name="title"]')?.focus();
    }
};

const openMemoCreateDialog = () => openMemoDialog(memoCreateDialog);

if (addMemoButton && memoCreateDialog && memoBodyInput) {
    addMemoButton.addEventListener("click", openMemoCreateDialog);
}

memoCreateCloseButton?.addEventListener("click", () => {
    memoCreateDialog?.close();
});

if (memoCreateDialog?.dataset.openOnLoad === "true") {
    openMemoCreateDialog();
}

emptyTrashButton?.addEventListener("click", () => {
    emptyTrashDialog?.showModal();
});

const openMemoEditDialog = (dialogId) => {
    openMemoDialog(document.getElementById(dialogId));
};

const positionMemoActionMenu = (button) => {
    if (!memoActionMenu) {
        return;
    }

    memoActionMenu.hidden = false;
    const buttonRect = button.getBoundingClientRect();
    const menuRect = memoActionMenu.getBoundingClientRect();
    const gap = 6;
    const edge = 8;
    let left = buttonRect.right - menuRect.width;
    left = Math.max(
        edge,
        Math.min(left, window.innerWidth - menuRect.width - edge)
    );
    let top = buttonRect.bottom + gap;
    if (top + menuRect.height > window.innerHeight - edge) {
        top = buttonRect.top - menuRect.height - gap;
    }
    memoActionMenu.style.left = `${left}px`;
    memoActionMenu.style.top = `${Math.max(edge, top)}px`;
};

memoMenuButtons.forEach((button) => {
    button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (
            memoActionMenu
            && activeMemoMenuButton === button
            && !memoActionMenu.hidden
        ) {
            closeMemoActionMenu();
            return;
        }

        closeMemoActionMenu();
        activeMemoMenuButton = button;
        button.setAttribute("aria-expanded", "true");
        if (memoActionMenuEdit && memoActionMenuTrashForm) {
            memoActionMenuEdit.dataset.dialogId = button.dataset.dialogId;
            memoActionMenuTrashForm.action = button.dataset.trashUrl;
        }
        positionMemoActionMenu(button);
    });
});

memoCardOpenButtons.forEach((button) => {
    button.addEventListener("click", () => {
        openMemoEditDialog(button.dataset.dialogId);
    });
});

memoActionMenuEdit?.addEventListener("click", () => {
    openMemoEditDialog(memoActionMenuEdit.dataset.dialogId);
});

memoDialogCloseButtons.forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
});

document.querySelectorAll(
    '.shop-memo-edit-dialog[data-open-on-load="true"]'
).forEach(openMemoDialog);

const contextFromMemoElement = (element) => {
    const card = element.closest("article[data-memo-id]");
    const editor = element.closest("dialog[data-memo-editor]");
    const source = card || editor;
    if (!source?.dataset.memoId) {
        return null;
    }

    const title = card
        ? card.querySelector(".shop-memo-title")?.textContent || ""
        : editor.querySelector('input[name="title"]')?.value || "";
    const body = card
        ? card.querySelector(".shop-memo-body")?.textContent || ""
        : editor.querySelector('textarea[name="body"]')?.value || "";

    return {
        memoId: source.dataset.memoId,
        pinUrl: source.dataset.pinUrl,
        trashUrl: source.dataset.trashUrl,
        restoreUrl: source.dataset.restoreUrl,
        pinned: source.dataset.pinned === "true",
        title,
        body,
        card,
        editor,
    };
};

const closeMobileActionSheet = () => {
    if (mobileActionSheet?.open) {
        mobileActionSheet.close();
    }
    activeMobileMemo = null;
};

const openMobileActionSheet = (element) => {
    if (!MOBILE_MEMO_MEDIA.matches || !mobileActionSheet) {
        return;
    }

    const context = contextFromMemoElement(element);
    if (!context) {
        return;
    }

    activeMobileMemo = context;
    mobilePreviewTitle.textContent = context.title;
    mobilePreviewBody.textContent = context.body;
    mobilePinAction.textContent = context.pinned
        ? "📌 ピン留め解除"
        : "📌 ピン留め";
    mobileActionSheet.showModal();
    mobilePinAction.focus();
};

const setActionButtonsDisabled = (disabled) => {
    [mobilePinAction, mobileCopyAction, mobileTrashAction].forEach(
        (button) => {
            if (button) {
                button.disabled = disabled;
            }
        }
    );
};

const removeMemoFromList = (memoId) => {
    const card = document.querySelector(
        `article[data-memo-id="${memoId}"]`
    );
    card?.closest(".shop-memo-swipe-row")?.remove();
    document.getElementById(`shop-memo-edit-dialog-${memoId}`)?.remove();

    const remainingCount = document.querySelectorAll(
        "article.shop-memo-card"
    ).length;
    document.querySelectorAll(".shop-memo-count-desktop").forEach(
        (count) => {
            count.textContent = `${remainingCount}件のメモ`;
        }
    );
    document.querySelectorAll(".shop-memo-count-mobile").forEach(
        (count) => {
            count.textContent = `(${remainingCount}件)`;
        }
    );

    const trashLink = document.querySelector(
        ".shop-memo-mobile-trash-link"
    );
    const trashCount = trashLink?.querySelector(
        ".shop-memo-mobile-trash-count"
    );
    if (trashLink && trashCount) {
        const nextTrashCount = Number.parseInt(trashCount.textContent, 10) + 1;
        trashCount.textContent = String(nextTrashCount);
        trashLink.setAttribute(
            "aria-label",
            `ゴミ箱を開く（${nextTrashCount}件）`
        );
    }
};

const performPin = async (context) => {
    try {
        await postMemoJson(context.pinUrl, {pinned: !context.pinned});
        window.location.assign(mobileConfig.dataset.listUrl);
    } catch (error) {
        showSnackbar(error.message);
    }
};

const performTrash = async (context) => {
    try {
        const result = await postMemoJson(context.trashUrl);
        context.editor?.close();
        removeMemoFromList(context.memoId);
        showSnackbar("メモを削除しました", result.restore_url);
    } catch (error) {
        showSnackbar(error.message);
    }
};

mobilePinAction?.addEventListener("click", async () => {
    if (!activeMobileMemo) {
        return;
    }
    setActionButtonsDisabled(true);
    const context = activeMobileMemo;
    closeMobileActionSheet();
    await performPin(context);
    setActionButtonsDisabled(false);
});

const copyTextToClipboard = async (text) => {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
    }

    const fallbackTextarea = document.createElement("textarea");
    fallbackTextarea.value = text;
    fallbackTextarea.readOnly = true;
    fallbackTextarea.style.position = "fixed";
    fallbackTextarea.style.left = "-9999px";
    fallbackTextarea.style.opacity = "0";
    document.body.appendChild(fallbackTextarea);
    fallbackTextarea.focus();
    fallbackTextarea.select();
    fallbackTextarea.setSelectionRange(0, fallbackTextarea.value.length);

    let copied = false;
    try {
        copied = document.execCommand("copy");
    } finally {
        fallbackTextarea.remove();
    }
    if (!copied) {
        throw new Error("コピーできませんでした。");
    }
};

mobileCopyAction?.addEventListener("click", async () => {
    if (!activeMobileMemo) {
        return;
    }
    setActionButtonsDisabled(true);
    const context = activeMobileMemo;
    closeMobileActionSheet();
    try {
        await copyTextToClipboard(context.body);
        showSnackbar(
            "コピーしました",
            null,
            MOBILE_MEMO_SETTINGS.COPY_FEEDBACK_DURATION_MS
        );
    } catch (error) {
        showSnackbar(
            error.message || "コピーできませんでした。",
            null,
            MOBILE_MEMO_SETTINGS.COPY_FEEDBACK_DURATION_MS
        );
    } finally {
        setActionButtonsDisabled(false);
    }
});

mobileTrashAction?.addEventListener("click", async () => {
    if (!activeMobileMemo) {
        return;
    }
    setActionButtonsDisabled(true);
    const context = activeMobileMemo;
    closeMobileActionSheet();
    await performTrash(context);
    setActionButtonsDisabled(false);
});

mobileActionClose?.addEventListener("click", closeMobileActionSheet);
mobileActionSheet?.addEventListener("click", (event) => {
    if (event.target === mobileActionSheet) {
        closeMobileActionSheet();
    }
});

const resetSwipePosition = (card) => {
    card.style.transition = "transform 0.18s ease";
    card.style.transform = "translateX(0)";
    window.setTimeout(() => {
        card.style.transition = "";
    }, 180);
};

document.querySelectorAll(".shop-memo-swipe-row").forEach((row) => {
    const card = row.querySelector("article.shop-memo-card");
    if (!card) {
        return;
    }

    let gesture = null;
    let longPressTimer = null;
    let suppressClickUntil = 0;

    const cancelLongPress = () => {
        if (longPressTimer) {
            window.clearTimeout(longPressTimer);
            longPressTimer = null;
        }
    };

    row.addEventListener("touchstart", (event) => {
        if (
            !MOBILE_MEMO_MEDIA.matches
            || event.touches.length !== 1
            || event.target.closest("a, .shop-memo-menu-button")
        ) {
            return;
        }

        const touch = event.touches[0];
        gesture = {
            startX: touch.clientX,
            startY: touch.clientY,
            startTime: performance.now(),
            horizontal: false,
            vertical: false,
        };
        longPressTimer = window.setTimeout(() => {
            if (!gesture || gesture.horizontal || gesture.vertical) {
                return;
            }
            suppressClickUntil = Date.now() + 600;
            openMobileActionSheet(card);
            gesture = null;
        }, MOBILE_MEMO_SETTINGS.LONG_PRESS_MS);
    }, {passive: true});

    row.addEventListener("touchmove", (event) => {
        if (!gesture || event.touches.length !== 1) {
            return;
        }

        const touch = event.touches[0];
        const deltaX = touch.clientX - gesture.startX;
        const deltaY = touch.clientY - gesture.startY;
        const absoluteX = Math.abs(deltaX);
        const absoluteY = Math.abs(deltaY);

        if (
            Math.hypot(deltaX, deltaY)
            >= MOBILE_MEMO_SETTINGS.MOVE_CANCEL_PX
        ) {
            cancelLongPress();
        }

        if (!gesture.horizontal && !gesture.vertical) {
            if (
                absoluteY
                > absoluteX * MOBILE_MEMO_SETTINGS.DIRECTION_BIAS
            ) {
                gesture.vertical = true;
                return;
            }
            if (
                absoluteX
                > absoluteY * MOBILE_MEMO_SETTINGS.DIRECTION_BIAS
                && absoluteX >= MOBILE_MEMO_SETTINGS.MOVE_CANCEL_PX
            ) {
                gesture.horizontal = true;
            }
        }

        if (!gesture.horizontal) {
            return;
        }

        event.preventDefault();
        const maximumMovement = card.offsetWidth * 0.82;
        const boundedDelta = Math.max(
            -maximumMovement,
            Math.min(deltaX, maximumMovement)
        );
        card.style.transform = `translateX(${boundedDelta}px)`;
    }, {passive: false});

    const finishGesture = (event) => {
        cancelLongPress();
        if (!gesture) {
            resetSwipePosition(card);
            return;
        }

        const touch = event.changedTouches?.[0];
        if (!touch || !gesture.horizontal) {
            gesture = null;
            resetSwipePosition(card);
            return;
        }

        const deltaX = touch.clientX - gesture.startX;
        const elapsed = Math.max(performance.now() - gesture.startTime, 1);
        const velocity = deltaX / elapsed;
        const distanceRatio = Math.abs(deltaX) / card.offsetWidth;
        const shouldDelete = deltaX < 0 && (
            distanceRatio >= MOBILE_MEMO_SETTINGS.DELETE_DISTANCE_RATIO
            || (
                distanceRatio >= MOBILE_MEMO_SETTINGS.FAST_SWIPE_MIN_RATIO
                && velocity <= -MOBILE_MEMO_SETTINGS.FAST_SWIPE_VELOCITY
            )
        );
        const shouldPin = deltaX > 0 && (
            distanceRatio >= MOBILE_MEMO_SETTINGS.PIN_DISTANCE_RATIO
        );

        gesture = null;
        resetSwipePosition(card);
        if (!shouldDelete && !shouldPin) {
            return;
        }

        suppressClickUntil = Date.now() + 600;
        const context = contextFromMemoElement(card);
        if (!context) {
            return;
        }

        if (shouldDelete) {
            performTrash(context);
        } else {
            performPin(context);
        }
    };

    row.addEventListener("touchend", finishGesture, {passive: true});
    row.addEventListener("touchcancel", () => {
        cancelLongPress();
        gesture = null;
        resetSwipePosition(card);
    }, {passive: true});
    row.addEventListener("click", (event) => {
        if (Date.now() < suppressClickUntil) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);
    row.addEventListener("contextmenu", (event) => {
        if (MOBILE_MEMO_MEDIA.matches) {
            event.preventDefault();
        }
    });
});

document.addEventListener("click", (event) => {
    if (
        memoActionMenu
        && !memoActionMenu.hidden
        && !memoActionMenu.contains(event.target)
    ) {
        closeMemoActionMenu();
    }
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
        closeMemoActionMenu();
    }
});

window.addEventListener("scroll", closeMemoActionMenu, true);
window.addEventListener("resize", () => {
    closeMemoActionMenu();
    document.querySelectorAll("dialog[data-memo-editor]").forEach(
        refreshMobileEditorLayout
    );
});
window.visualViewport?.addEventListener("resize", () => {
    document.querySelectorAll("dialog[data-memo-editor][open]").forEach(
        refreshMobileEditorLayout
    );
});
MOBILE_MEMO_MEDIA.addEventListener("change", () => {
    document.querySelectorAll("dialog[data-memo-editor]").forEach(
        refreshMobileEditorLayout
    );
});
