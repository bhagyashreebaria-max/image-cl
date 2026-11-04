package main

import (
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"mime/multipart"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	defaultHost                 = "127.0.0.1"
	defaultPort                 = 8000
	defaultWorkerPort           = 8001
	maxInputBytes         int64 = 80 << 20
	maxRequestBytes       int64 = maxInputBytes + (2 << 20)
	maxFormMemory               = 8 << 20
	maxJSONResponse             = 4 << 20
	workerResponseTimeout       = 15 * time.Minute
	workerHealthTimeout         = 2 * time.Second
	workerStartupTimeout        = 60 * time.Second
)

//go:embed app/static
var staticFiles embed.FS

type apiError struct {
	Detail string `json:"detail"`
}

type apiServer struct {
	workerURL     *url.URL
	worker        *http.Client
	health        *http.Client
	allowedOrigin map[string]struct{}
	allowedHost   map[string]struct{}
	firstOrigin   string
}

func newAPIServer(workerURL *url.URL, origins, hosts string) *apiServer {
	allowed := make(map[string]struct{})
	allowedHosts := make(map[string]struct{})
	firstOrigin := ""
	for _, origin := range strings.Split(origins, ",") {
		origin = strings.TrimSpace(origin)
		if origin == "" {
			continue
		}
		if firstOrigin == "" {
			firstOrigin = origin
		}
		allowed[origin] = struct{}{}
	}
	for _, host := range strings.Split(hosts, ",") {
		host = strings.ToLower(strings.TrimSpace(host))
		if host != "" {
			allowedHosts[host] = struct{}{}
		}
	}

	transport := &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: (&net.Dialer{
			Timeout:   5 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          100,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: workerResponseTimeout,
		ExpectContinueTimeout: time.Second,
	}
	return &apiServer{
		workerURL:     workerURL,
		worker:        &http.Client{Transport: transport},
		health:        &http.Client{Timeout: workerHealthTimeout},
		allowedOrigin: allowed,
		allowedHost:   allowedHosts,
		firstOrigin:   firstOrigin,
	}
}

func (g *apiServer) handler() http.Handler {
	staticRoot, err := fs.Sub(staticFiles, "app/static")
	if err != nil {
		panic(err)
	}

	mux := http.NewServeMux()
	mux.Handle("GET /health", methodOnly(http.MethodGet, http.HandlerFunc(g.healthCheck)))
	mux.Handle("/api", methodOnly(http.MethodGet, http.HandlerFunc(g.apiIndex)))
	mux.Handle("/api/tools", methodOnly(http.MethodGet, http.HandlerFunc(g.tools)))
	mux.Handle("/api/status", methodOnly(http.MethodGet, http.HandlerFunc(g.status)))
	mux.Handle("/api/inspect", methodOnly(http.MethodPost, http.HandlerFunc(g.inspect)))
	mux.Handle("/api/process/{tool}", methodOnly(http.MethodPost, http.HandlerFunc(g.process)))
	mux.Handle("/api/", http.HandlerFunc(g.notFound))
	mux.Handle("/", http.FileServer(http.FS(staticRoot)))
	return g.limitAPIRequests(g.protectAPIOrigin(g.protectHost(mux)))
}

func methodOnly(method string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != method {
			w.Header().Set("Allow", method)
			writeJSON(w, http.StatusMethodNotAllowed, apiError{Detail: "Method not allowed."})
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (g *apiServer) protectAPIOrigin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api") {
			next.ServeHTTP(w, r)
			return
		}
		origin := r.Header.Get("Origin")
		if origin != "" {
			if _, allowed := g.allowedOrigin[origin]; !allowed {
				writeJSON(w, http.StatusForbidden, apiError{Detail: "Requests must come from a configured application Origin."})
				return
			}
		} else if r.Method == http.MethodPost {
			writeJSON(w, http.StatusForbidden, apiError{Detail: "An allowed application Origin header is required."})
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (g *apiServer) protectHost(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if _, allowed := g.allowedHost["*"]; !allowed {
			host := strings.ToLower(r.Host)
			if parsed, err := url.Parse("//" + host); err == nil {
				host = parsed.Hostname()
			}
			if _, allowed := g.allowedHost[host]; !allowed {
				writeJSON(w, http.StatusBadRequest, apiError{Detail: "Request Host is not configured for this service."})
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

func (g *apiServer) limitAPIRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api/") || r.Method != http.MethodPost || r.Body == nil {
			next.ServeHTTP(w, r)
			return
		}
		if r.ContentLength > maxRequestBytes {
			writeJSON(w, http.StatusRequestEntityTooLarge, apiError{Detail: "Request exceeds the 80 MiB input limit plus multipart overhead."})
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, maxRequestBytes)
		next.ServeHTTP(w, r)
	})
}

func (g *apiServer) apiIndex(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"name":        "Papertrail PDF API",
		"description": "A single Go API for the PDF toolkit. Discover available operations and processing capabilities at /api/tools.",
		"endpoints": map[string]any{
			"tools": map[string]string{
				"method":      http.MethodGet,
				"path":        "/api/tools",
				"description": "List PDF operations, categories, and runtime availability.",
			},
			"status": map[string]string{
				"method":      http.MethodGet,
				"path":        "/api/status",
				"description": "Read processing capabilities and upload/concurrency limits.",
			},
			"process": map[string]string{
				"method":      http.MethodPost,
				"path":        "/api/process/{tool}",
				"description": "Run a catalog operation with multipart/form-data; upload inputs as files and pass operation options as form fields.",
			},
			"inspect": map[string]string{
				"method":      http.MethodPost,
				"path":        "/api/inspect",
				"description": "Inspect a PDF using multipart/form-data with a single file field.",
			},
		},
		"health":            "/health",
		"max_input_bytes":   maxInputBytes,
		"max_request_bytes": maxRequestBytes,
		"error_response":    apiError{Detail: "A human-readable error message."},
	})
}

func (g *apiServer) tools(w http.ResponseWriter, r *http.Request) {
	g.workerJSON(w, r, "/api/tools")
}

func (g *apiServer) status(w http.ResponseWriter, r *http.Request) {
	g.workerJSON(w, r, "/api/status")
}

func (g *apiServer) inspect(w http.ResponseWriter, r *http.Request) {
	g.forwardMultipart(w, r, "/api/inspect", "file")
}

func (g *apiServer) process(w http.ResponseWriter, r *http.Request) {
	tool := r.PathValue("tool")
	if tool == "" || strings.Contains(tool, "/") {
		writeJSON(w, http.StatusNotFound, apiError{Detail: "That tool is not available."})
		return
	}
	g.forwardMultipart(w, r, "/api/process/"+url.PathEscape(tool), "files")
}

func (g *apiServer) notFound(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusNotFound, apiError{Detail: "API endpoint not found."})
}

func (g *apiServer) healthCheck(w http.ResponseWriter, r *http.Request) {
	request, err := http.NewRequestWithContext(r.Context(), http.MethodGet, g.workerURL.String()+"/health", nil)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, apiError{Detail: "Could not check the processing worker."})
		return
	}
	response, err := g.health.Do(request)
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "unavailable"})
		return
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "unavailable"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (g *apiServer) workerJSON(w http.ResponseWriter, r *http.Request, workerPath string) {
	request, err := http.NewRequestWithContext(r.Context(), http.MethodGet, g.workerURL.String()+workerPath, nil)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, apiError{Detail: "Could not create the worker request."})
		return
	}
	request.Header.Set("Origin", g.firstOrigin)
	response, err := g.worker.Do(request)
	if err != nil {
		g.workerError(w, err)
		return
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, maxJSONResponse+1))
	if err != nil {
		writeJSON(w, http.StatusBadGateway, apiError{Detail: "Could not read the processing worker response."})
		return
	}
	if int64(len(body)) > maxJSONResponse {
		writeJSON(w, http.StatusBadGateway, apiError{Detail: "Processing worker returned an oversized response."})
		return
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		writeJSON(w, response.StatusCode, workerErrorDetail(body, response.StatusCode))
		return
	}
	if !json.Valid(body) {
		writeJSON(w, http.StatusBadGateway, apiError{Detail: "Processing worker returned an invalid JSON response."})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(response.StatusCode)
	_, _ = w.Write(body)
}

func (g *apiServer) forwardMultipart(w http.ResponseWriter, r *http.Request, workerPath, uploadField string) {
	r.Body = http.MaxBytesReader(w, r.Body, maxRequestBytes)
	if err := r.ParseMultipartForm(maxFormMemory); err != nil {
		var maxBytesError *http.MaxBytesError
		if errors.As(err, &maxBytesError) {
			writeJSON(w, http.StatusRequestEntityTooLarge, apiError{Detail: "Request exceeds the 80 MiB input limit plus multipart overhead."})
			return
		}
		writeJSON(w, http.StatusBadRequest, apiError{Detail: "Request must use valid multipart form data."})
		return
	}
	if r.MultipartForm != nil {
		defer r.MultipartForm.RemoveAll()
	}

	files := r.MultipartForm.File[uploadField]
	if uploadField == "file" && len(files) != 1 {
		writeJSON(w, http.StatusBadRequest, apiError{Detail: "Provide exactly one input file in the file field."})
		return
	}
	if uploadField == "files" && len(files) == 0 {
		writeJSON(w, http.StatusBadRequest, apiError{Detail: "Provide at least one input file in the files field."})
		return
	}
	if len(files) > 100 {
		writeJSON(w, http.StatusRequestEntityTooLarge, apiError{Detail: "An operation can include at most 100 files."})
		return
	}
	totalSize := int64(0)
	for _, file := range files {
		totalSize += file.Size
		if totalSize > maxInputBytes {
			writeJSON(w, http.StatusRequestEntityTooLarge, apiError{Detail: "Combined input files exceed the 80 MiB server limit."})
			return
		}
	}
	for fieldName, fieldFiles := range r.MultipartForm.File {
		if fieldName != uploadField && len(fieldFiles) > 0 {
			writeJSON(w, http.StatusBadRequest, apiError{Detail: "Unexpected file field in request."})
			return
		}
	}

	reader, writer := io.Pipe()
	formWriter := multipart.NewWriter(writer)
	request, err := http.NewRequestWithContext(r.Context(), http.MethodPost, g.workerURL.String()+workerPath, reader)
	if err != nil {
		reader.Close()
		writer.Close()
		writeJSON(w, http.StatusInternalServerError, apiError{Detail: "Could not create the processing request."})
		return
	}
	request.Header.Set("Content-Type", formWriter.FormDataContentType())
	request.Header.Set("Origin", g.firstOrigin)

	writeDone := make(chan error, 1)
	go func() {
		writeDone <- writeMultipartForm(formWriter, writer, r.MultipartForm, uploadField, files)
	}()
	response, err := g.worker.Do(request)
	if err != nil {
		_ = reader.CloseWithError(err)
		<-writeDone
		g.workerError(w, err)
		return
	}
	defer response.Body.Close()
	_ = reader.Close()
	writeErr := <-writeDone
	if writeErr != nil && response.StatusCode >= 200 && response.StatusCode < 300 {
		writeJSON(w, http.StatusBadRequest, apiError{Detail: "Could not read or forward the uploaded files."})
		return
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		body, readErr := io.ReadAll(io.LimitReader(response.Body, maxJSONResponse+1))
		if readErr != nil || int64(len(body)) > maxJSONResponse {
			writeJSON(w, http.StatusBadGateway, apiError{Detail: "Could not read the processing worker error response."})
			return
		}
		writeJSON(w, response.StatusCode, workerErrorDetail(body, response.StatusCode))
		return
	}
	copyResponseHeaders(w.Header(), response.Header)
	w.WriteHeader(response.StatusCode)
	if _, err := io.Copy(w, response.Body); err != nil {
		log.Printf("stream PDF output to client: %v", err)
	}
}

func writeMultipartForm(formWriter *multipart.Writer, pipe *io.PipeWriter, form *multipart.Form, uploadField string, files []*multipart.FileHeader) error {
	for name, values := range form.Value {
		for _, value := range values {
			if err := formWriter.WriteField(name, value); err != nil {
				pipe.CloseWithError(err)
				return err
			}
		}
	}
	for _, header := range files {
		file, err := header.Open()
		if err != nil {
			pipe.CloseWithError(err)
			return err
		}
		part, createErr := formWriter.CreateFormFile(uploadField, filepath.Base(header.Filename))
		if createErr == nil {
			_, createErr = io.Copy(part, file)
		}
		closeErr := file.Close()
		if createErr != nil {
			pipe.CloseWithError(createErr)
			return createErr
		}
		if closeErr != nil {
			pipe.CloseWithError(closeErr)
			return closeErr
		}
	}
	if err := formWriter.Close(); err != nil {
		pipe.CloseWithError(err)
		return err
	}
	return pipe.Close()
}

func (g *apiServer) workerError(w http.ResponseWriter, err error) {
	if errors.Is(err, context.DeadlineExceeded) {
		writeJSON(w, http.StatusGatewayTimeout, apiError{Detail: "The processing worker did not respond before the request timeout."})
		return
	}
	var networkError net.Error
	if errors.As(err, &networkError) && networkError.Timeout() {
		writeJSON(w, http.StatusGatewayTimeout, apiError{Detail: "The processing worker did not respond before the request timeout."})
		return
	}
	if errors.Is(err, context.Canceled) {
		return
	}
	log.Printf("PDF worker request failed: %v", err)
	writeJSON(w, http.StatusServiceUnavailable, apiError{Detail: "The PDF processing worker is unavailable."})
}

func workerErrorDetail(body []byte, status int) apiError {
	var failure map[string]json.RawMessage
	if json.Unmarshal(body, &failure) == nil {
		if detail, ok := failure["detail"]; ok {
			var message string
			if json.Unmarshal(detail, &message) == nil && message != "" {
				return apiError{Detail: message}
			}
			if len(detail) > 0 && string(detail) != "null" {
				return apiError{Detail: "Request failed: " + string(detail)}
			}
		}
	}
	return apiError{Detail: fmt.Sprintf("The processing worker returned HTTP %d.", status)}
}

func copyResponseHeaders(destination, source http.Header) {
	for _, name := range []string{"Content-Type", "Content-Disposition", "X-Input-Bytes", "X-Output-Bytes"} {
		if value := source.Get(name); value != "" {
			destination.Set(name, value)
		}
	}
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		log.Printf("could not write JSON response: %v", err)
	}
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	host, err := envString("PAPERTRAIL_HOST", defaultHost)
	if err != nil {
		return err
	}
	port, err := envPort("PAPERTRAIL_PORT", defaultPort)
	if err != nil {
		return err
	}
	workerPort, err := envPort("PAPERTRAIL_WORKER_PORT", defaultWorkerPort)
	if err != nil {
		return err
	}
	python, err := envString("PAPERTRAIL_PYTHON_COMMAND", "python")
	if err != nil {
		return err
	}
	startupTimeout, err := envDuration("PAPERTRAIL_WORKER_STARTUP_TIMEOUT", workerStartupTimeout)
	if err != nil {
		return err
	}

	origins := os.Getenv("PAPERTRAIL_ALLOWED_ORIGINS")
	if origins == "" {
		origins = defaultAllowedOrigins(host, port)
	}
	if err := validateAllowedOrigins(origins); err != nil {
		return err
	}
	allowedHosts := os.Getenv("PAPERTRAIL_ALLOWED_HOSTS")
	if allowedHosts == "" {
		allowedHosts = "127.0.0.1,localhost,testserver"
	}
	if err := validateAllowedHosts(allowedHosts); err != nil {
		return err
	}
	if port == workerPort {
		return errors.New("PAPERTRAIL_PORT and PAPERTRAIL_WORKER_PORT must be different")
	}
	workerURL := &url.URL{Scheme: "http", Host: net.JoinHostPort("127.0.0.1", strconv.Itoa(workerPort))}
	address := net.JoinHostPort(host, strconv.Itoa(port))

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	workerDone, err := startWorker(ctx, python, workerPort, origins)
	if err != nil {
		return err
	}
	defer stop()
	if err := waitForWorker(ctx, workerURL, workerDone, startupTimeout); err != nil {
		return err
	}

	server := &http.Server{
		Addr:              address,
		Handler:           newAPIServer(workerURL, origins, allowedHosts).handler(),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       2 * time.Minute,
		MaxHeaderBytes:    1 << 20,
	}
	serverDone := make(chan error, 1)
	go func() {
		serverDone <- server.ListenAndServe()
	}()

	select {
	case <-ctx.Done():
		shutdownContext, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownContext); err != nil {
			return fmt.Errorf("shut down API: %w", err)
		}
		return nil
	case err := <-workerDone:
		if err == nil {
			return errors.New("PDF processing worker stopped unexpectedly")
		}
		return fmt.Errorf("PDF processing worker stopped: %w", err)
	case err := <-serverDone:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return fmt.Errorf("serve PDF API: %w", err)
	}
}

func startWorker(ctx context.Context, python string, workerPort int, origins string) (<-chan error, error) {
	command := exec.CommandContext(ctx, python, "-m", "app")
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	command.Env = append(os.Environ(),
		"PAPERTRAIL_HOST=127.0.0.1",
		"PAPERTRAIL_PORT="+strconv.Itoa(workerPort),
		"PAPERTRAIL_ALLOWED_ORIGINS="+origins,
		"PAPERTRAIL_ALLOWED_HOSTS=127.0.0.1,localhost,testserver",
	)
	if err := command.Start(); err != nil {
		return nil, fmt.Errorf("start Python PDF worker: %w", err)
	}
	done := make(chan error, 1)
	go func() {
		done <- command.Wait()
	}()
	return done, nil
}

func defaultAllowedOrigins(host string, port int) string {
	hosts := []string{host}
	if host == "0.0.0.0" || host == "::" || host == "" {
		hosts = []string{"127.0.0.1", "localhost"}
	}
	origins := make([]string, 0, len(hosts))
	for _, originHost := range hosts {
		origins = append(origins, "http://"+net.JoinHostPort(originHost, strconv.Itoa(port)))
	}
	return strings.Join(origins, ",")
}

func validateAllowedOrigins(origins string) error {
	found := false
	for _, origin := range strings.Split(origins, ",") {
		origin = strings.TrimSpace(origin)
		if origin == "" {
			continue
		}
		parsed, err := url.Parse(origin)
		if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" {
			return errors.New("PAPERTRAIL_ALLOWED_ORIGINS must contain comma-separated http(s) origins without paths")
		}
		found = true
	}
	if !found {
		return errors.New("PAPERTRAIL_ALLOWED_ORIGINS must include at least one http(s) origin")
	}
	return nil
}

func validateAllowedHosts(hosts string) error {
	for _, host := range strings.Split(hosts, ",") {
		host = strings.TrimSpace(host)
		if host == "*" {
			return nil
		}
		if host == "" {
			continue
		}
		if strings.ContainsAny(host, "/\\:") {
			return errors.New("PAPERTRAIL_ALLOWED_HOSTS must contain hostnames without schemes, paths, or ports")
		}
	}
	for _, host := range strings.Split(hosts, ",") {
		if strings.TrimSpace(host) != "" {
			return nil
		}
	}
	return errors.New("PAPERTRAIL_ALLOWED_HOSTS must include at least one hostname")
}

func waitForWorker(ctx context.Context, workerURL *url.URL, done <-chan error, timeout time.Duration) error {
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	client := &http.Client{Timeout: workerHealthTimeout}
	for {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, workerURL.String()+"/health", nil)
		if err == nil {
			response, requestErr := client.Do(request)
			if requestErr == nil {
				response.Body.Close()
				if response.StatusCode == http.StatusOK {
					return nil
				}
			}
		}
		select {
		case err := <-done:
			if err == nil {
				return errors.New("PDF processing worker exited before becoming ready")
			}
			return fmt.Errorf("PDF processing worker exited before becoming ready: %w", err)
		case <-ctx.Done():
			return ctx.Err()
		case <-deadline.C:
			return fmt.Errorf("PDF processing worker did not become ready within %s", timeout)
		case <-ticker.C:
		}
	}
}

func envString(name, fallback string) (string, error) {
	value := os.Getenv(name)
	if value == "" {
		return fallback, nil
	}
	if strings.ContainsAny(value, "\r\n") {
		return "", fmt.Errorf("%s must not contain line breaks", name)
	}
	return value, nil
}

func envPort(name string, fallback int) (int, error) {
	value := os.Getenv(name)
	if value == "" {
		return fallback, nil
	}
	port, err := strconv.Atoi(value)
	if err != nil || port < 1 || port > 65535 {
		return 0, fmt.Errorf("%s must be a port from 1 to 65535", name)
	}
	return port, nil
}

func envDuration(name string, fallback time.Duration) (time.Duration, error) {
	value := os.Getenv(name)
	if value == "" {
		return fallback, nil
	}
	duration, err := time.ParseDuration(value)
	if err != nil || duration <= 0 {
		return 0, fmt.Errorf("%s must be a positive Go duration, such as 90s", name)
	}
	return duration, nil
}
