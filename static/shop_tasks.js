const TASK_SETTINGS = Object.freeze({
    LONG_PRESS_MS: 500,
    SELECTION_REORDER_PRESS_MS: 320,
    MOVE_CANCEL_PX: 10,
    SNACKBAR_DURATION_MS: 7000,
});

const taskRoot = document.querySelector("[data-task-list-root]");
const addTaskButton = document.querySelector(".shop-tools-fab");
const taskTitleInput = document.getElementById("shop-task-title");

if (addTaskButton && taskTitleInput) {
    addTaskButton.addEventListener("click", () => taskTitleInput.focus());
}

if (taskRoot) {
    const incompleteList = taskRoot.querySelector(
        '[data-task-list="incomplete"]'
    );
    const completedList = taskRoot.querySelector(
        '[data-task-list="completed"]'
    );
    const completedGroup = taskRoot.querySelector(
        "[data-task-completed-group]"
    );
    const selectionToolbar = taskRoot.querySelector(
        "[data-task-selection-toolbar]"
    );
    const selectionCount = taskRoot.querySelector(
        "[data-task-selection-count]"
    );
    const selectAllButton = taskRoot.querySelector("[data-task-select-all]");
    const bulkDeleteButton = taskRoot.querySelector(
        "[data-task-bulk-delete]"
    );
    const selectionCancelButton = taskRoot.querySelector(
        "[data-task-selection-cancel]"
    );
    const startSelectionButton = taskRoot.querySelector(
        "[data-task-start-selection]"
    );
    const clearCompletedForm = taskRoot.querySelector(
        "[data-task-clear-completed]"
    );
    const moreMenu = taskRoot.querySelector(".shop-task-more-menu");
    const snackbar = document.querySelector("[data-task-snackbar]");
    const snackbarMessage = document.querySelector(
        "[data-task-snackbar-message]"
    );
    const snackbarUndo = document.querySelector(
        "[data-task-snackbar-undo]"
    );

    let selectionMode = false;
    let activeGesture = null;
    let snackbarTimer = null;
    let undoCompletion = null;

    const cards = () => Array.from(
        taskRoot.querySelectorAll(".shop-task-card")
    );

    const parseJsonResponse = async (response) => {
        try {
            return await response.json();
        } catch (_error) {
            return {};
        }
    };

    const postTaskJson = async (url, payload = {}) => {
        if (!url || !taskRoot.dataset.csrfToken) {
            throw new Error("操作の準備ができていません。");
        }
        const response = await fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": taskRoot.dataset.csrfToken,
            },
            body: JSON.stringify(payload),
        });
        const data = await parseJsonResponse(response);
        if (!response.ok || data.ok !== true) {
            throw new Error(data.error || "操作を完了できませんでした。");
        }
        return data;
    };

    const hideSnackbar = () => {
        if (!snackbar) {
            return;
        }
        snackbar.hidden = true;
        undoCompletion = null;
        if (snackbarTimer) {
            window.clearTimeout(snackbarTimer);
            snackbarTimer = null;
        }
    };

    const showSnackbar = (message, undo = null) => {
        if (!snackbar || !snackbarMessage || !snackbarUndo) {
            return;
        }
        if (snackbarTimer) {
            window.clearTimeout(snackbarTimer);
        }
        snackbarMessage.textContent = message;
        undoCompletion = undo;
        snackbarUndo.hidden = !undo;
        snackbar.hidden = false;
        snackbarTimer = window.setTimeout(
            hideSnackbar,
            TASK_SETTINGS.SNACKBAR_DURATION_MS
        );
    };

    const updateCounts = () => {
        const incompleteCount = incompleteList.children.length;
        const completedCount = completedList.children.length;
        const totalCount = incompleteCount + completedCount;
        taskRoot.querySelector("[data-task-incomplete-count]").textContent = (
            incompleteCount
        );
        taskRoot.querySelector("[data-task-completed-count]").textContent = (
            completedCount
        );
        taskRoot.querySelector("[data-task-total-count]").textContent = (
            `${totalCount}件`
        );
        taskRoot.querySelector("[data-task-incomplete-empty]").hidden = (
            incompleteCount > 0
        );
        taskRoot.querySelector("[data-task-completed-empty]").hidden = (
            completedCount > 0
        );
        taskRoot.querySelector("[data-task-empty]").hidden = totalCount > 0;
        const clearButton = clearCompletedForm?.querySelector("button");
        if (clearButton) {
            clearButton.disabled = completedCount === 0;
        }

        if (completedGroup) {
            const wasHidden = completedGroup.hidden;

            if (completedCount === 0) {
                completedGroup.hidden = true;
                completedGroup.open = false;
            } else {
                completedGroup.hidden = false;

                /*
                 * 0件 → 最初の1件になった時だけ自動展開。
                 * ユーザーが自分で折り畳んだ後は勝手に開かない。
                 */
                if (wasHidden) {
                    completedGroup.open = true;
                }
            }
        }
    };

    const insertCardByPosition = (card, list) => {
        const position = Number(card.dataset.taskPosition);
        const nextCard = Array.from(list.children).find((candidate) => (
            candidate !== card
            && Number(candidate.dataset.taskPosition) > position
        ));
        list.insertBefore(card, nextCard || null);
    };

    const setCompletionVisual = (
        card,
        completed,
        {moveCard = true} = {}
    ) => {
        card.dataset.taskCompleted = String(completed);
        card.classList.toggle("shop-task-card-completed", completed);

        const form = card.querySelector(".shop-task-completion-form");
        const button = card.querySelector(".shop-task-toggle");
        const title = card.dataset.taskTitle;

        form.querySelector('[name="completed"]').value = completed ? "0" : "1";
        button.setAttribute("aria-pressed", String(completed));
        button.setAttribute(
            "aria-label",
            `「${title}」を${completed ? "未完了に戻す" : "完了にする"}`
        );

        card.querySelector(".shop-task-icon-incomplete").hidden = completed;
        card.querySelector(".shop-task-icon-completed").hidden = !completed;

        if (moveCard) {
            insertCardByPosition(
                card,
                completed ? completedList : incompleteList
            );
            updateCounts();
        }
    };

    const waitForTaskMotion = (milliseconds) => new Promise(
        (resolve) => window.setTimeout(resolve, milliseconds)
    );

    const moveTaskCardSmoothly = async (card, targetList) => {
        if (
            targetList === completedList
            && completedGroup
            && !completedGroup.open
        ) {
            completedGroup.open = true;
        }

        const firstRect = card.getBoundingClientRect();

        insertCardByPosition(card, targetList);
        updateCounts();

        const lastRect = card.getBoundingClientRect();

        const deltaX = firstRect.left - lastRect.left;
        const deltaY = firstRect.top - lastRect.top;

        const reduceMotion = window.matchMedia(
            "(prefers-reduced-motion: reduce)"
        ).matches;

        if (
            reduceMotion
            || !card.animate
            || (
                Math.abs(deltaX) < 1
                && Math.abs(deltaY) < 1
            )
        ) {
            return;
        }

        const animation = card.animate(
            [
                {
                    transform: `translate(${deltaX}px, ${deltaY}px)`,
                    opacity: 0.82,
                },
                {
                    transform: "translate(0, 0)",
                    opacity: 1,
                },
            ],
            {
                duration: 360,
                easing: "cubic-bezier(0.22, 1, 0.36, 1)",
            }
        );

        try {
            await animation.finished;
        } catch (_error) {
            // animation cancel は無視
        }
    };

    const changeCompletion = async (card, completed) => {
        const previousState = card.dataset.taskCompleted === "true";

        if (previousState === completed) {
            return;
        }

        card.classList.add("is-completion-changing");

        /*
         * まずチェック状態だけ変える。
         * ここではまだ別リストへ移動しない。
         */
        setCompletionVisual(
            card,
            completed,
            {moveCard: false}
        );

        /*
         * 通信はすぐ開始するが、UIは少しだけチェックを見せる。
         * reject を即座に捕捉して unhandled rejection を防ぐ。
         */
        const request = postTaskJson(
            card.dataset.completionUrl,
            {completed}
        ).then(
            () => ({ok: true}),
            (error) => ({ok: false, error})
        );

        try {
            await waitForTaskMotion(140);

            await moveTaskCardSmoothly(
                card,
                completed ? completedList : incompleteList
            );

            const result = await request;

            if (!result.ok) {
                throw result.error;
            }
        } catch (error) {
            setCompletionVisual(
                card,
                previousState,
                {moveCard: false}
            );

            await moveTaskCardSmoothly(
                card,
                previousState ? completedList : incompleteList
            );

            showSnackbar(error.message);
        } finally {
            card.classList.remove("is-completion-changing");
        }
    };

    snackbarUndo?.addEventListener("click", async () => {
        if (!undoCompletion) {
            return;
        }
        const action = undoCompletion;
        hideSnackbar();
        await changeCompletion(action.card, action.completed, false);
    });

    const setFavoriteVisual = (card, starred) => {
        card.dataset.taskStarred = String(starred);
        const form = card.querySelector(".shop-task-favorite-form");
        const button = card.querySelector(".shop-task-star");
        const icon = button.querySelector("svg");
        form.querySelector('[name="starred"]').value = starred ? "0" : "1";
        button.classList.toggle("is-starred", starred);
        button.setAttribute("aria-pressed", String(starred));
        button.setAttribute(
            "aria-label",
            `「${card.dataset.taskTitle}」を${
                starred ? "お気に入りから外す" : "お気に入りにする"
            }`
        );
        icon.setAttribute("fill", starred ? "currentColor" : "none");
    };

    const updateTaskTitle = (card, title) => {
        card.dataset.taskTitle = title;
        card.querySelector(".shop-task-title").textContent = title;
        card.querySelector(".shop-task-edit-trigger").setAttribute(
            "aria-label",
            `「${title}」を編集`
        );
        card.querySelector(".shop-task-selection-control").setAttribute(
            "aria-label",
            `「${title}」を選択`
        );
        setCompletionVisual(
            card,
            card.dataset.taskCompleted === "true"
        );
        setFavoriteVisual(card, card.dataset.taskStarred === "true");
    };

    const closeEditor = (card, restoreValue = true) => {
        const trigger = card.querySelector(".shop-task-edit-trigger");
        const titleElement = card.querySelector(".shop-task-title");

        if (restoreValue) {
            titleElement.textContent = card.dataset.taskTitle;
        }

        titleElement.setAttribute("contenteditable", "false");
        titleElement.classList.remove("is-editing");
        trigger.classList.remove("is-editing");
        trigger.setAttribute("role", "button");
        trigger.setAttribute("tabindex", "0");
    };

    const saveDirectEditor = async (card) => {
        if (card.classList.contains("shop-task-card-draft")) {
            return;
        }

        const titleElement = card.querySelector(".shop-task-title");

        if (
            !titleElement
            || titleElement.getAttribute("contenteditable") !== "true"
            || card.dataset.titleSaving === "true"
        ) {
            return;
        }

        const title = titleElement.textContent.replace(/\\u00a0/g, " ").trim();

        if (!title || title.length > 100) {
            showSnackbar(
                title
                    ? "タスク名は100文字以内で入力してください。"
                    : "タスク名を入力してください。"
            );
            titleElement.focus();
            return;
        }

        if (title === card.dataset.taskTitle) {
            closeEditor(card, false);
            return;
        }

        card.dataset.titleSaving = "true";

        try {
            const data = await postTaskJson(card.dataset.editUrl, {title});
            updateTaskTitle(card, data.task.title);
            closeEditor(card, false);
        } catch (error) {
            titleElement.textContent = card.dataset.taskTitle;
            closeEditor(card, false);
            showSnackbar(error.message);
        } finally {
            delete card.dataset.titleSaving;
        }
    };

    const openEditor = (card) => {
        if (selectionMode) {
            return;
        }

        const trigger = card.querySelector(".shop-task-edit-trigger");
        const titleElement = card.querySelector(".shop-task-title");

        if (titleElement.getAttribute("contenteditable") === "true") {
            return;
        }

        titleElement.setAttribute("contenteditable", "true");
        titleElement.classList.add("is-editing");
        trigger.classList.add("is-editing");
        trigger.removeAttribute("role");
        trigger.removeAttribute("tabindex");

        window.requestAnimationFrame(() => {
            titleElement.focus();

            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(titleElement);
            range.collapse(false);

            selection.removeAllRanges();
            selection.addRange(range);
        });
    };

    const selectedCards = () => cards().filter((card) => (
        card.classList.contains("is-selected")
    ));

    const updateSelectionToolbar = () => {
        const selectedCount = selectedCards().length;
        const taskCount = cards().length;
        const allSelected = taskCount > 0 && selectedCount === taskCount;

        selectionCount.textContent = selectedCount > 0
            ? `${selectedCount}件選択済み`
            : "アイテムを選択";

        bulkDeleteButton.disabled = selectedCount === 0;

        selectAllButton.classList.toggle("is-all-selected", allSelected);
        selectAllButton.setAttribute(
            "aria-label",
            allSelected ? "すべて解除" : "すべて選択"
        );
        selectAllButton.setAttribute(
            "aria-pressed",
            String(allSelected)
        );
    };

    const setCardSelected = (card, selected) => {
        card.classList.toggle("is-selected", selected);
        const control = card.querySelector(".shop-task-selection-control");
        control.setAttribute("aria-pressed", String(selected));
        control.querySelector(".shop-task-selection-off").hidden = selected;
        control.querySelector(".shop-task-selection-on").hidden = !selected;
        updateSelectionToolbar();
    };

    const enterSelectionMode = (initialCard = null) => {
        if (!selectionMode) {
            selectionMode = true;
            taskRoot.classList.add("is-selection-mode");
            selectionToolbar.hidden = false;
            cards().forEach((card) => {
                closeEditor(card);
                card.querySelector(".shop-task-selection-control").hidden = false;
            });
            moreMenu?.removeAttribute("open");
        }
        if (initialCard) {
            setCardSelected(initialCard, true);
        } else {
            updateSelectionToolbar();
        }
    };

    const leaveSelectionMode = () => {
        selectionMode = false;
        taskRoot.classList.remove("is-selection-mode");
        selectionToolbar.hidden = true;
        cards().forEach((card) => {
            card.classList.remove("is-selected");
            const control = card.querySelector(
                ".shop-task-selection-control"
            );
            control.hidden = true;
            control.setAttribute("aria-pressed", "false");
            control.querySelector(".shop-task-selection-off").hidden = false;
            control.querySelector(".shop-task-selection-on").hidden = true;
        });
    };

    /*
     * 「…」メニューは外側を触ったら閉じる。
     * メニュー内の操作中は閉じない。
     */
    document.addEventListener("pointerdown", (event) => {
        if (!moreMenu?.open || moreMenu.contains(event.target)) {
            return;
        }

        moreMenu.removeAttribute("open");
    });

    startSelectionButton?.addEventListener("click", () => enterSelectionMode());
    selectionCancelButton?.addEventListener("click", leaveSelectionMode);

    selectAllButton?.addEventListener("click", () => {
        const shouldSelect = selectedCards().length !== cards().length;
        cards().forEach((card) => setCardSelected(card, shouldSelect));
    });

    bulkDeleteButton?.addEventListener("click", async () => {
        const selected = selectedCards();
        if (!selected.length) {
            return;
        }
        if (!window.confirm(
            `選択した${selected.length}件のタスクを削除しますか？`
        )) {
            return;
        }
        bulkDeleteButton.disabled = true;
        try {
            await postTaskJson(taskRoot.dataset.bulkDeleteUrl, {
                task_ids: selected.map((card) => Number(card.dataset.taskId)),
            });
            selected.forEach((card) => card.remove());
            leaveSelectionMode();
            updateCounts();
            showSnackbar(`${selected.length}件のタスクを削除しました`);
        } catch (error) {
            bulkDeleteButton.disabled = false;
            showSnackbar(error.message);
        }
    });

    clearCompletedForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const completedCards = Array.from(completedList.children);
        if (!completedCards.length || !window.confirm(
            "完了済みのタスクをすべて削除しますか？"
        )) {
            return;
        }
        const button = clearCompletedForm.querySelector("button");
        button.disabled = true;
        try {
            await postTaskJson(clearCompletedForm.action);
            completedCards.forEach((card) => card.remove());
            updateCounts();
            moreMenu?.removeAttribute("open");
            showSnackbar("完了済みのタスクを削除しました");
        } catch (error) {
            button.disabled = false;
            showSnackbar(error.message);
        }
    });

    taskRoot.addEventListener("submit", async (event) => {
        const completionForm = event.target.closest(
            ".shop-task-completion-form"
        );
        if (completionForm) {
            event.preventDefault();
            const card = completionForm.closest(".shop-task-card");
            await changeCompletion(
                card,
                card.dataset.taskCompleted !== "true"
            );
            return;
        }

        const favoriteForm = event.target.closest(".shop-task-favorite-form");
        if (favoriteForm) {
            event.preventDefault();
            const card = favoriteForm.closest(".shop-task-card");
            const previousState = card.dataset.taskStarred === "true";
            setFavoriteVisual(card, !previousState);
            try {
                await postTaskJson(card.dataset.favoriteUrl, {
                    starred: !previousState,
                });
            } catch (error) {
                setFavoriteVisual(card, previousState);
                showSnackbar(error.message);
            }
            return;
        }

        const editForm = event.target.closest(".shop-task-inline-edit");
        if (!editForm) {
            return;
        }
        event.preventDefault();
        const card = editForm.closest(".shop-task-card");
        const input = editForm.querySelector('[name="title"]');
        const title = input.value.trim();
        if (!title || title.length > 100) {
            showSnackbar(
                title ? "タスク名は100文字以内で入力してください。" : "タスク名を入力してください。"
            );
            input.focus();
            return;
        }
        const saveButton = editForm.querySelector(".shop-task-edit-save");
        saveButton.disabled = true;
        try {
            const data = await postTaskJson(card.dataset.editUrl, {title});
            updateTaskTitle(card, data.task.title);
            closeEditor(card, false);
        } catch (error) {
            showSnackbar(error.message);
        } finally {
            saveButton.disabled = false;
        }
    });

    taskRoot.addEventListener("keydown", (event) => {
        const titleElement = event.target.closest(
            '.shop-task-title[contenteditable="true"]'
        );

        if (!titleElement) {
            return;
        }

        const card = titleElement.closest(".shop-task-card");

        if (event.key === "Enter") {
            event.preventDefault();
            titleElement.blur();
        } else if (event.key === "Escape") {
            event.preventDefault();
            closeEditor(card, true);
        }
    });

    taskRoot.addEventListener("focusout", (event) => {
        const titleElement = event.target.closest(
            '.shop-task-title[contenteditable="true"]'
        );

        if (!titleElement) {
            return;
        }

        const card = titleElement.closest(".shop-task-card");

        window.setTimeout(() => {
            if (
                titleElement.getAttribute("contenteditable") === "true"
                && document.activeElement !== titleElement
            ) {
                saveDirectEditor(card);
            }
        }, 0);
    });

    taskRoot.addEventListener("click", (event) => {
        const card = event.target.closest(".shop-task-card");
        if (!card) {
            return;
        }
        if (card.dataset.suppressClick === "true") {
            event.preventDefault();
            event.stopPropagation();
            delete card.dataset.suppressClick;
            return;
        }
        if (selectionMode) {
            event.preventDefault();
            event.stopPropagation();
            setCardSelected(card, !card.classList.contains("is-selected"));
            return;
        }
        if (event.target.closest(".shop-task-edit-trigger")) {
            openEditor(card);
        } else if (event.target.closest(".shop-task-edit-cancel")) {
            closeEditor(card);
        }
    }, true);

    const currentOrder = () => [
        ...Array.from(incompleteList.children),
        ...Array.from(completedList.children),
    ].map((card) => Number(card.dataset.taskId));

    const restoreOrder = (taskIds) => {
        const byId = new Map(cards().map((card) => [
            Number(card.dataset.taskId),
            card,
        ]));
        taskIds.forEach((taskId) => {
            const card = byId.get(taskId);
            const list = card.dataset.taskCompleted === "true"
                ? completedList
                : incompleteList;
            list.append(card);
        });
    };

    const saveOrder = async (previousOrder) => {
        const taskIds = currentOrder();
        try {
            await postTaskJson(taskRoot.dataset.reorderUrl, {
                task_ids: taskIds,
            });
            taskIds.forEach((taskId, position) => {
                taskRoot.querySelector(
                    `[data-task-id="${taskId}"]`
                ).dataset.taskPosition = String(position);
            });
        } catch (error) {
            restoreOrder(previousOrder);
            showSnackbar(error.message);
        }
    };

    const createReorderGhost = (gesture) => {
        const card = gesture.card;
        const rect = card.getBoundingClientRect();
        const ghost = card.cloneNode(true);

        ghost.classList.add("shop-task-drag-ghost");
        ghost.classList.remove(
            "is-pressing",
            "is-reordering",
            "is-reorder-source"
        );
        ghost.setAttribute("aria-hidden", "true");

        ghost.querySelectorAll("[id]").forEach((element) => {
            element.removeAttribute("id");
        });

        ghost.style.left = `${rect.left}px`;
        ghost.style.top = `${rect.top}px`;
        ghost.style.width = `${rect.width}px`;
        ghost.style.height = `${rect.height}px`;

        document.body.appendChild(ghost);

        gesture.ghost = ghost;
        gesture.pointerOffsetY = gesture.startY - rect.top;
        gesture.reordering = true;

        document.documentElement.classList.add("shop-task-reorder-lock");
        document.body.classList.add("shop-task-reorder-lock");

        card.classList.remove("is-pressing");
        card.classList.add(
            "is-reordering",
            "is-reorder-source"
        );
    };

    const clearReorderVisual = (gesture) => {
        if (!gesture) {
            return;
        }

        gesture.ghost?.remove();

        gesture.reordering = false;
        document.documentElement.classList.remove("shop-task-reorder-lock");
        document.body.classList.remove("shop-task-reorder-lock");

        gesture.card?.classList.remove(
            "is-pressing",
            "is-reordering",
            "is-reorder-source"
        );
    };

    document.addEventListener(
        "touchmove",
        (event) => {
            if (activeGesture?.reordering) {
                event.preventDefault();
            }
        },
        {passive: false}
    );

    const animateTaskReorder = (list, sourceCard, moveCard) => {
        const candidates = Array.from(list.children).filter(
            (card) => (
                card !== sourceCard
                && card.classList.contains("shop-task-card")
            )
        );

        const previousPositions = new Map(
            candidates.map((card) => [
                card,
                card.getBoundingClientRect().top,
            ])
        );

        moveCard();

        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            return;
        }

        candidates.forEach((card) => {
            const previousTop = previousPositions.get(card);
            const currentTop = card.getBoundingClientRect().top;
            const deltaY = previousTop - currentTop;

            if (Math.abs(deltaY) < 1) {
                return;
            }

            card.animate(
                [
                    {transform: `translateY(${deltaY}px)`},
                    {transform: "translateY(0)"},
                ],
                {
                    duration: 220,
                    easing: "cubic-bezier(0.22, 1, 0.36, 1)",
                }
            );
        });
    };

    const moveReorderGesture = (gesture, clientY) => {
        const card = gesture.card;
        const list = gesture.list;

        if (!list) {
            return;
        }

        if (gesture.ghost) {
            gesture.ghost.style.top = `${
                clientY - gesture.pointerOffsetY
            }px`;
        }

        const candidates = Array.from(list.children).filter(
            (candidate) => (
                candidate !== card
                && candidate.classList.contains("shop-task-card")
            )
        );

        let insertBefore = null;

        for (const candidate of candidates) {
            const rect = candidate.getBoundingClientRect();
            const midpoint = rect.top + (rect.height / 2);

            if (clientY < midpoint) {
                insertBefore = candidate;
                break;
            }
        }

        const orderWillChange = insertBefore
            ? card.nextElementSibling !== insertBefore
            : card !== list.lastElementChild;

        if (!orderWillChange) {
            return;
        }

        animateTaskReorder(list, card, () => {
            if (insertBefore) {
                list.insertBefore(card, insertBefore);
            } else {
                list.append(card);
            }
        });

        gesture.reordered = (
            currentOrder().join(",")
            !== gesture.previousOrder.join(",")
        );
    };

    const cancelGesture = () => {
        if (!activeGesture) {
            return;
        }

        const gesture = activeGesture;

        if (gesture.timer) {
            window.clearTimeout(gesture.timer);
        }

        if (gesture.reordered) {
            restoreOrder(gesture.previousOrder);
        }

        clearReorderVisual(gesture);
        activeGesture = null;
    };

    const startLongPressGesture = (
        card,
        clientX,
        clientY,
        inputType,
        inputId
    ) => {
        cancelGesture();

        card.classList.add("is-pressing");

        const delay = selectionMode
            ? TASK_SETTINGS.SELECTION_REORDER_PRESS_MS
            : TASK_SETTINGS.LONG_PRESS_MS;

        const gesture = {
            card,
            list: card.parentElement,
            inputType,
            inputId,
            startX: clientX,
            startY: clientY,
            armed: false,
            reordered: false,
            previousOrder: currentOrder(),
            ghost: null,
            pointerOffsetY: 0,
            timer: null,
        };

        gesture.timer = window.setTimeout(() => {
            if (activeGesture !== gesture) {
                return;
            }

            gesture.armed = true;
            card.classList.remove("is-pressing");

            /*
             * 通常状態からの長押しなら、
             * 選択モードへ入り、このTaskを選択。
             *
             * 指は離さないので、この直後から
             * そのままドラッグできる。
             */
            if (!selectionMode) {
                enterSelectionMode(card);
            }

            window.getSelection()?.removeAllRanges();
        }, delay);

        activeGesture = gesture;
    };

    const moveLongPressGesture = (
        clientX,
        clientY,
        preventDefault
    ) => {
        if (!activeGesture) {
            return;
        }

        const gesture = activeGesture;

        const distance = Math.hypot(
            clientX - gesture.startX,
            clientY - gesture.startY
        );

        /*
         * 長押し成立前に指が動いた場合は、
         * 並び替えではなく通常スクロールを優先。
         */
        if (!gesture.armed) {
            if (distance > TASK_SETTINGS.MOVE_CANCEL_PX) {
                cancelGesture();
            }
            return;
        }

        /*
         * 長押し成立後は数px動けばドラッグ開始。
         */
        if (Math.abs(clientY - gesture.startY) < 4) {
            return;
        }

        preventDefault();

        if (!gesture.ghost) {
            createReorderGhost(gesture);
        }

        moveReorderGesture(gesture, clientY);
    };

    const suppressSyntheticClick = (card) => {
        card.dataset.suppressClick = "true";

        window.setTimeout(() => {
            if (card.dataset.suppressClick === "true") {
                delete card.dataset.suppressClick;
            }
        }, 300);
    };

    const finishGesture = () => {
        if (!activeGesture) {
            return;
        }

        const gesture = activeGesture;

        if (gesture.timer) {
            window.clearTimeout(gesture.timer);
        }

        activeGesture = null;

        clearReorderVisual(gesture);

        if (gesture.armed) {
            suppressSyntheticClick(gesture.card);
        }

        if (gesture.reordered) {
            saveOrder(gesture.previousOrder);
        }
    };

    const isGestureBlockedTarget = (target) => (
        target.closest(
            ".shop-task-toggle, "
            + ".shop-task-star, "
            + ".shop-task-inline-edit, "
            + ".shop-task-selection-control, "
            + '.shop-task-title[contenteditable="true"]'
        )
    );

    /*
     * Android / iPhone:
     * touchmoveをpassive:falseで扱うことで、
     * 長押し成立前はスクロール、
     * 長押し成立後は同じ指でドラッグできる。
     */
    taskRoot.addEventListener("touchstart", (event) => {
        if (event.touches.length !== 1) {
            return;
        }

        const card = event.target.closest(".shop-task-card");

        if (
            !card
            || card.classList.contains("shop-task-card-draft")
            || isGestureBlockedTarget(event.target)
        ) {
            return;
        }

        const touch = event.touches[0];

        startLongPressGesture(
            card,
            touch.clientX,
            touch.clientY,
            "touch",
            touch.identifier
        );
    }, {passive: true});

    document.addEventListener("touchmove", (event) => {
        if (
            !activeGesture
            || activeGesture.inputType !== "touch"
        ) {
            return;
        }

        const touch = Array.from(event.touches).find(
            (candidate) => (
                candidate.identifier === activeGesture.inputId
            )
        );

        if (!touch) {
            return;
        }

        moveLongPressGesture(
            touch.clientX,
            touch.clientY,
            () => event.preventDefault()
        );
    }, {passive: false});

    document.addEventListener("touchend", (event) => {
        if (
            !activeGesture
            || activeGesture.inputType !== "touch"
        ) {
            return;
        }

        const ended = Array.from(event.changedTouches).some(
            (touch) => (
                touch.identifier === activeGesture.inputId
            )
        );

        if (ended) {
            finishGesture();
        }
    });

    document.addEventListener("touchcancel", () => {
        if (activeGesture?.inputType === "touch") {
            cancelGesture();
        }
    });

    /*
     * マウス / ペンも同じ長押し→ドラッグ操作。
     */
    taskRoot.addEventListener("pointerdown", (event) => {
        if (
            event.pointerType === "touch"
            || event.button !== 0
        ) {
            return;
        }

        const card = event.target.closest(".shop-task-card");

        if (
            !card
            || card.classList.contains("shop-task-card-draft")
            || isGestureBlockedTarget(event.target)
        ) {
            return;
        }

        startLongPressGesture(
            card,
            event.clientX,
            event.clientY,
            "pointer",
            event.pointerId
        );
    });

    document.addEventListener("pointermove", (event) => {
        if (
            !activeGesture
            || activeGesture.inputType !== "pointer"
            || activeGesture.inputId !== event.pointerId
        ) {
            return;
        }

        moveLongPressGesture(
            event.clientX,
            event.clientY,
            () => event.preventDefault()
        );
    });

    document.addEventListener("pointerup", (event) => {
        if (
            activeGesture?.inputType === "pointer"
            && activeGesture.inputId === event.pointerId
        ) {
            finishGesture();
        }
    });

    document.addEventListener("pointercancel", (event) => {
        if (
            activeGesture?.inputType === "pointer"
            && activeGesture.inputId === event.pointerId
        ) {
            cancelGesture();
        }
    });

    // === Task direct-create from FAB ===

    const removeDraftTask = (card) => {
        if (!card?.classList.contains("shop-task-card-draft")) {
            return;
        }
        card.remove();
    };

    const persistDraftTask = async (card) => {
        if (
            !card
            || !card.classList.contains("shop-task-card-draft")
            || card.dataset.saving === "true"
        ) {
            return;
        }

        const titleElement = card.querySelector(".shop-task-title");
        const title = titleElement.textContent
            .replace(/\u00a0/g, " ")
            .trim();

        if (!title) {
            removeDraftTask(card);
            return;
        }

        if (title.length > 100) {
            showSnackbar("タスク名は100文字以内で入力してください。");
            titleElement.focus();
            return;
        }

        card.dataset.saving = "true";

        try {
            const data = await postTaskJson(
                taskRoot.dataset.createUrl,
                {title}
            );

            const task = data.task;

            /*
             * DB側では既存positionが+1されるため、
             * ブラウザ側も同期させる。
             */
            cards().forEach((candidate) => {
                if (candidate === card) {
                    return;
                }

                const position = Number(candidate.dataset.taskPosition);
                if (Number.isFinite(position)) {
                    candidate.dataset.taskPosition = String(position + 1);
                }
            });

            card.classList.remove("shop-task-card-draft");
            card.dataset.taskId = String(task.id);
            card.dataset.taskTitle = task.title;
            card.dataset.taskPosition = String(task.position);
            card.dataset.taskCompleted = "false";
            card.dataset.taskStarred = "false";
            card.dataset.completionUrl = task.completion_url;
            card.dataset.editUrl = task.edit_url;
            card.dataset.favoriteUrl = task.favorite_url;

            const completionForm = card.querySelector(
                ".shop-task-completion-form"
            );
            completionForm.action = task.completion_url;

            const completionButton = completionForm.querySelector(
                ".shop-task-toggle"
            );
            completionButton.disabled = false;
            completionButton.type = "submit";
            completionButton.setAttribute(
                "aria-label",
                `「${task.title}」を完了にする`
            );

            const favoriteForm = card.querySelector(
                ".shop-task-favorite-form"
            );
            favoriteForm.action = task.favorite_url;

            titleElement.textContent = task.title;
            closeEditor(card, false);

            updateCounts();
        } catch (error) {
            showSnackbar(error.message);
            titleElement.focus();
        } finally {
            delete card.dataset.saving;
        }
    };

    const createDraftTask = () => {
        if (selectionMode) {
            return;
        }

        const existingDraft = taskRoot.querySelector(
            ".shop-task-card-draft"
        );

        if (existingDraft) {
            existingDraft.querySelector(".shop-task-title").focus();
            return;
        }

        const card = document.createElement("article");
        card.className = "shop-task-card shop-task-card-draft";
        card.dataset.taskTitle = "";
        card.dataset.taskCompleted = "false";
        card.dataset.taskStarred = "false";
        card.dataset.taskPosition = "-1";

        const reorderHandle = document.createElement("button");
        reorderHandle.type = "button";
        reorderHandle.className = (
            "shop-task-reorder-handle touch-control-no-select"
        );
        reorderHandle.setAttribute("aria-label", "タスクを並び替え");

        const svgNamespace = "http://www.w3.org/2000/svg";
        const reorderIcon = document.createElementNS(
            svgNamespace,
            "svg"
        );
        reorderIcon.setAttribute("data-icon", "grip-vertical");
        reorderIcon.setAttribute("viewBox", "0 0 24 24");
        reorderIcon.setAttribute("fill", "currentColor");
        reorderIcon.setAttribute("focusable", "false");
        reorderIcon.setAttribute("aria-hidden", "true");

        [
            [9, 5],
            [15, 5],
            [9, 12],
            [15, 12],
            [9, 19],
            [15, 19],
        ].forEach(([cx, cy]) => {
            const circle = document.createElementNS(
                svgNamespace,
                "circle"
            );
            circle.setAttribute("cx", String(cx));
            circle.setAttribute("cy", String(cy));
            circle.setAttribute("r", "1.5");
            reorderIcon.appendChild(circle);
        });

        reorderHandle.appendChild(reorderIcon);

        const completionForm = document.createElement("form");
        completionForm.className = "shop-task-completion-form";

        const completedInput = document.createElement("input");
        completedInput.type = "hidden";
        completedInput.name = "completed";
        completedInput.value = "1";

        const completionButton = document.createElement("button");
        completionButton.type = "button";
        completionButton.className = (
            "shop-task-toggle touch-control-no-select"
        );
        completionButton.setAttribute(
            "aria-label",
            "未保存のタスク"
        );
        completionButton.setAttribute("aria-pressed", "false");
        completionButton.disabled = true;

        const incompleteIcon = document.createElement("span");
        incompleteIcon.className = "shop-task-icon-incomplete";

        const completedIcon = document.createElement("span");
        completedIcon.className = "shop-task-icon-completed";
        completedIcon.hidden = true;

        completionButton.append(
            incompleteIcon,
            completedIcon
        );
        completionForm.append(
            completedInput,
            completionButton
        );

        const editTrigger = document.createElement("div");
        editTrigger.className = (
            "shop-task-edit-trigger is-editing"
        );

        const titleElement = document.createElement("span");
        titleElement.className = "shop-task-title is-editing";
        titleElement.setAttribute("contenteditable", "true");
        titleElement.spellcheck = false;

        editTrigger.appendChild(titleElement);

        const favoriteForm = document.createElement("form");
        favoriteForm.className = "shop-task-favorite-form";
        favoriteForm.hidden = true;

        const starredInput = document.createElement("input");
        starredInput.type = "hidden";
        starredInput.name = "starred";
        starredInput.value = "1";

        const favoriteButton = document.createElement("button");
        favoriteButton.type = "button";
        favoriteButton.className = "shop-task-star";
        favoriteButton.setAttribute("aria-pressed", "false");

        const favoriteIcon = document.createElementNS(
            svgNamespace,
            "svg"
        );
        favoriteIcon.setAttribute("aria-hidden", "true");

        favoriteButton.appendChild(favoriteIcon);
        favoriteForm.append(
            starredInput,
            favoriteButton
        );

        const selectionControl = document.createElement("button");
        selectionControl.type = "button";
        selectionControl.className = (
            "shop-task-selection-control touch-control-no-select"
        );
        selectionControl.setAttribute(
            "aria-label",
            "タスクを選択"
        );
        selectionControl.setAttribute("aria-pressed", "false");
        selectionControl.hidden = true;

        const selectionOff = document.createElement("span");
        selectionOff.className = "shop-task-selection-off";

        const selectionOn = document.createElement("span");
        selectionOn.className = "shop-task-selection-on";
        selectionOn.hidden = true;

        selectionControl.append(
            selectionOff,
            selectionOn
        );

        card.append(
            reorderHandle,
            completionForm,
            editTrigger,
            favoriteForm,
            selectionControl
        );

        incompleteList.insertBefore(
            card,
            incompleteList.firstElementChild
        );


        /*
         * Escapeだけは保存せず仮Taskを破棄。
         */
        titleElement.addEventListener(
            "keydown",
            (event) => {
                if (event.key !== "Escape") {
                    return;
                }

                event.preventDefault();
                event.stopImmediatePropagation();
                removeDraftTask(card);
            },
            true
        );

        titleElement.addEventListener("focusout", () => {
            window.setTimeout(() => {
                if (
                    card.isConnected
                    && document.activeElement !== titleElement
                ) {
                    persistDraftTask(card);
                }
            }, 0);
        });

        window.requestAnimationFrame(() => {
            titleElement.focus({preventScroll: true});
        });
    };

    addTaskButton?.addEventListener("click", (event) => {
        event.preventDefault();
        createDraftTask();
    });

    completedGroup?.addEventListener("toggle", () => cancelGesture());
    updateCounts();
}

// === Task UI cleanup 3 ===
(() => {
    const createSection = document.querySelector(".shop-task-create-section");
    const fab = document.querySelector(".shop-tools-fab");
    const createInput = document.getElementById("shop-task-title");

    const closeComposer = () => {
        if (!createSection) {
            return;
        }
        createSection.classList.remove("is-open");
        document.body.classList.remove("shop-task-composer-open");
    };

    const openComposer = () => {
        if (!createSection || !createInput) {
            return;
        }

        createSection.classList.add("is-open");
        document.body.classList.add("shop-task-composer-open");

        window.requestAnimationFrame(() => {
            createInput.focus({preventScroll: true});
        });
    };

    if (fab && createSection && createInput) {
        fab.addEventListener("click", () => {
            openComposer();
        });
    }

    /*
     * 追加欄が空のままフォーカスを外したら閉じる。
     * 入力中なら勝手に閉じない。
     */
    createSection?.addEventListener("focusout", () => {
        window.setTimeout(() => {
            if (
                !createSection.contains(document.activeElement)
                && !createInput.value.trim()
            ) {
                closeComposer();
            }
        }, 0);
    });

    /*
     * 編集はその場で完結。
     * Enter / スマホキーボードの完了 -> 保存
     * Escape -> キャンセル
     * 外をタップ -> 変更があれば保存
     */
    document.addEventListener("keydown", (event) => {
        const input = event.target.closest(".shop-task-inline-edit input");
        if (!input) {
            return;
        }

        const form = input.closest(".shop-task-inline-edit");

        if (event.key === "Escape") {
            event.preventDefault();
            form.querySelector(".shop-task-edit-cancel")?.click();
        }
    });

    document.addEventListener("focusout", (event) => {
        const input = event.target.closest(".shop-task-inline-edit input");
        if (!input) {
            return;
        }

        window.setTimeout(() => {
            if (!input.isConnected || document.activeElement === input) {
                return;
            }

            const form = input.closest(".shop-task-inline-edit");
            const card = input.closest(".shop-task-card");

            if (!form || !card || form.hidden) {
                return;
            }

            const title = input.value.trim();

            if (title && title !== card.dataset.taskTitle) {
                form.requestSubmit();
            } else {
                form.querySelector(".shop-task-edit-cancel")?.click();
            }
        }, 80);
    });
})();
