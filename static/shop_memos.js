const memoSearchInput = document.getElementById("memo-search");
const memoSearchClearButton = document.getElementById("memo-search-clear");
const addMemoButton = document.querySelector(".shop-tools-fab");
const memoBodyInput = document.getElementById("shop-memo-body");

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

if (addMemoButton && memoBodyInput) {
    addMemoButton.addEventListener("click", () => {
        memoBodyInput.focus();
    });
}
