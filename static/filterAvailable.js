$(document).ready(function () {
    $("#availableNowCheckbox").change(function () {
        let onlyAvailable = $(this).is(":checked");

        $(".book-item").each(function () {
            let isAvailable = false;

            // Check each availability item within the current book item
            $(this).find(".availability-item").each(function () {
                let status = $(this).data("status"); // This will get the string status
                console.log(status)

                // Check if the status is "Available"
                if (status && status.includes("Available")) {
                    isAvailable = true;
                }
            });

            // Show or hide the book item based on availability
            if (onlyAvailable && !isAvailable) {
                $(this).addClass("hidden");
            } else {
                $(this).removeClass("hidden");
            }
        });
    });
});
