// Keep shared browser behavior in one place as the application grows.
document.addEventListener("DOMContentLoaded", () => {
    console.log("Inventory-AI frontend loaded.");

    const itemsContainer = document.querySelector("#purchase-items");
    const addItemButton = document.querySelector("#add-item");
    const itemTemplate = document.querySelector("#purchase-item-template");
    if (!itemsContainer || !addItemButton || !itemTemplate) {
        return;
    }

    const updateTotals = () => {
        let total = 0;
        itemsContainer.querySelectorAll(".purchase-item-row").forEach((row) => {
            const quantity = Number(row.querySelector(".item-quantity").value) || 0;
            const unitCost = Number(row.querySelector(".item-unit-cost").value) || 0;
            const lineTotal = quantity * unitCost;
            row.querySelector(".item-line-total").textContent = `₹${lineTotal.toFixed(2)}`;
            total += lineTotal;
        });
        document.querySelector("#purchase-total").textContent = `₹${total.toFixed(2)}`;
    };

    const bindRow = (row) => {
        row.querySelectorAll("input").forEach((input) => {
            input.addEventListener("input", updateTotals);
        });
        row.querySelector(".remove-item").addEventListener("click", () => {
            if (itemsContainer.querySelectorAll(".purchase-item-row").length > 1) {
                row.remove();
                updateTotals();
            }
        });
    };

    itemsContainer.querySelectorAll(".purchase-item-row").forEach(bindRow);
    addItemButton.addEventListener("click", () => {
        const row = itemTemplate.content.firstElementChild.cloneNode(true);
        itemsContainer.appendChild(row);
        bindRow(row);
        updateTotals();
    });
    updateTotals();
});
