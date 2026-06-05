FROM golang:1.22 AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /orders .

FROM gcr.io/distroless/static
COPY --from=build /orders /orders
COPY migrations /migrations
EXPOSE 9090
ENTRYPOINT ["/orders"]
