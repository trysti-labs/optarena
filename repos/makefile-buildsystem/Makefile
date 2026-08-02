CC := gcc
CFLAGS := -std=c11 -Wall -MMD -MP -Isrc/core -Isrc/utils

CORE_SRCS := $(wildcard src/core/*.c)
UTILS_SRCS := $(wildcard src/utils/*.c)
CLI_SRCS := $(wildcard src/cli/*.c)
SRCS := $(CORE_SRCS) $(UTILS_SRCS) $(CLI_SRCS)
OBJS := $(SRCS:.c=.o)
DEPS := $(OBJS:.o=.d)

BINDIR := bin
TARGET := $(BINDIR)/app

TEST_SRCS := $(CORE_SRCS) $(UTILS_SRCS) tests/test_calc.c
TEST_TARGET := $(BINDIR)/test_calc

.PHONY: all clean test

all: $(TARGET)

$(TARGET): $(OBJS) | $(BINDIR)
	$(CC) $(OBJS) -o $(TARGET)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

$(BINDIR):
	mkdir -p $(BINDIR)

test: $(TEST_TARGET)
	./$(TEST_TARGET)

$(TEST_TARGET): $(TEST_SRCS) | $(BINDIR)
	$(CC) $(CFLAGS) $(TEST_SRCS) -o $(TEST_TARGET)

clean:
	rm -rf $(BINDIR) $(DEPS) $(OBJS)

-include $(DEPS)
