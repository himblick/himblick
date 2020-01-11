(function($) {
"use strict";

/**
 * Communicate with himblick over the websocket interface
 */
class HimblickSocket
{
    constructor() {
        this.socket = null;
        this.open();
        this.retry_interval = 1000; // ms
    }

    open() {
        if (this.socket !== null)
        {
            this.socket.onopen = null;
            this.socket.onmessage = null;
            this.socket.onclose = null;
            this.socket = null;
        }
        this.socket = new WebSocket(window.himblick.config.socket);
        this.socket.onopen = () => { this.on_open() };
        this.socket.onmessage = (evt) => { this.on_message(evt) };
        this.socket.onclose = () => { this.on_close() };
    }

    on_open() {
        console.debug("Websocket channel open");
    }

    on_close() {
        console.debug("Websocket channel closed");
        setTimeout(() => {this.open()}, this.retry_interval);
    }

    on_message(evt) {
        console.debug("Websocket message", evt);
        let new_evt = new CustomEvent("himblick.event", {
            detail: JSON.parse(evt.data),
        });
        document.dispatchEvent(new_evt);
    }
}

function main()
{
    window.himblick.socket = new HimblickSocket();

    document.addEventListener("himblick.event", evt => {
        if (evt.detail.event == "reload")
        {
            console.debug("Reloading page");
            window.location.reload(true);
        } else {
            console.log("Unknown event received", evt.detail);
        }
    });
}

$(main);

})(jQuery);
