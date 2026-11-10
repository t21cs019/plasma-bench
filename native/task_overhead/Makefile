# task_overhead を libgomp 版と libiomp5 版の2つ作る。
# libiomp5 版は plasmatest がリンクしているものと同じ libiomp5 を使う。
# plasmatest が libiomp5 を使っていなければ、libgomp 版だけを作る。
#   別の libiomp5 を使うとき: make IOMP5=/path/to/libiomp5.so

CC = gcc
CFLAGS = -O2 -Wall -Wextra
PLASMA_TEST ?= $(HOME)/Library/plasma/bin/plasmatest
IOMP5 ?= $(shell ldd $(PLASMA_TEST) 2>/dev/null | awk '/libiomp5/ {print $$3}')
BUILD = build

TARGETS = $(BUILD)/task_overhead_gomp
ifneq ($(strip $(IOMP5)),)
TARGETS += $(BUILD)/task_overhead_iomp5
endif

all: $(TARGETS)

$(BUILD):
	mkdir -p $@

$(BUILD)/task_overhead_gomp: task_overhead.c Makefile | $(BUILD)
	$(CC) $(CFLAGS) -fopenmp -DRUNTIME_NAME='"libgomp"' $< -o $@

# -fopenmp はコンパイルにだけ付ける（リンクに付けると libgomp がつながる）
$(BUILD)/task_overhead_iomp5: task_overhead.c Makefile | $(BUILD)
	$(CC) $(CFLAGS) -fopenmp -DRUNTIME_NAME='"libiomp5"' -c $< -o $(BUILD)/iomp5.o
	$(CC) $(BUILD)/iomp5.o -o $@ $(IOMP5) -Wl,-rpath,$(dir $(IOMP5)) -lpthread

clean:
	rm -rf $(BUILD)

.PHONY: all clean
