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

        const saleItemsContainer = document.querySelector("#sale-items");
        const addSaleItemButton = document.querySelector("#add-sale-item");
        const saleItemTemplate = document.querySelector("#sale-item-template");
        if (!saleItemsContainer || !addSaleItemButton || !saleItemTemplate) {
            return;
        }

        const updateSaleTotals = () => {
            let total = 0;
            saleItemsContainer.querySelectorAll(".sale-item-row").forEach((row) => {
                const quantity = Number(row.querySelector(".sale-quantity").value) || 0;
                const price = Number(row.querySelector(".sale-unit-price").value) || 0;
                const lineTotal = quantity * price;
                row.querySelector(".sale-line-total").textContent = `₹${lineTotal.toFixed(2)}`;
                total += lineTotal;
            });
            document.querySelector("#sale-total").textContent = `₹${total.toFixed(2)}`;
        };
        const bindSaleRow = (row) => {
            const product = row.querySelector(".sale-product");
            const updateProductInfo = () => {
                const option = product.options[product.selectedIndex];
                row.querySelector(".available-stock").textContent =
                    `Available stock: ${option?.dataset.stock || "—"}`;
                const price = row.querySelector(".sale-unit-price");
                if (option?.dataset.price && !price.value) {
                    price.value = Number(option.dataset.price).toFixed(2);
                }
            };
            row.querySelectorAll("input").forEach((input) => input.addEventListener("input", updateSaleTotals));
            product.addEventListener("change", () => { updateProductInfo(); updateSaleTotals(); });
            row.querySelector(".remove-sale-item").addEventListener("click", () => {
                if (saleItemsContainer.querySelectorAll(".sale-item-row").length > 1) {
                    row.remove();
                    updateSaleTotals();
                }
            });
            updateProductInfo();
        };
        saleItemsContainer.querySelectorAll(".sale-item-row").forEach(bindSaleRow);
        addSaleItemButton.addEventListener("click", () => {
            const row = saleItemTemplate.content.firstElementChild.cloneNode(true);
            saleItemsContainer.appendChild(row);
            bindSaleRow(row);
            updateSaleTotals();
        });
        updateSaleTotals();
    });
    updateTotals();
});
