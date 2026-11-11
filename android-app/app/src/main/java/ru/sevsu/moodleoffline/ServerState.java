package ru.sevsu.moodleoffline;

/** Общее состояние: адрес окна, когда Python-сервер готов, и текст ошибки, если он не запустился. */
final class ServerState {
    static volatile String url;       // http://127.0.0.1:порт/?t=ключ — пока null, сервер ещё запускается или остановлен
    static volatile String error;     // текст ошибки Python (показывается в окне)
    static volatile boolean running;  // рабочий поток Python жив

    private ServerState() {}
}
