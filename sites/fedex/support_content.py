"""Help for the offline shipping workflows, not a statement of carrier policy."""

SUPPORT_CONTENT = {
    "shipment-exception-status": {
        "summary": "Understand an exception scan and find the information needed to investigate it.",
        "body": (
            "Open the package's tracking detail and check its Timeline. Record the event time "
            "and event location of the latest exception, then read the explanation beneath it. "
            "A Shipment exception can describe a weather interruption. An Operational delay "
            "means address details are under review before a new delivery attempt. "
            "Use the Current ETA on the same page to see whether a delivery date is available."
        ),
        "topics": ["Tracking", "Delivery exceptions", "Address review"],
    },
    "weather-delay-guidance": {
        "summary": "Find the latest information for a shipment affected by a weather delay.",
        "body": (
            "Track the affected package and open its detail page. In the Timeline, locate the "
            "latest Shipment exception and read its event time, location and explanation. "
            "Compare that event with the Current ETA. A scan timestamp records an event; it "
            "is not a promised delivery time. If the ETA is pending weather clearance, there "
            "is no confirmed delivery date. Check the tracking detail again for an updated estimate."
        ),
        "topics": ["Tracking", "Weather disruptions", "Delivery estimates"],
    },
}
