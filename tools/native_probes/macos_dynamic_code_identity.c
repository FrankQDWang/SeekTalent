#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

struct code_statuses {
    OSStatus guest_status;
    OSStatus dynamic_status;
    OSStatus requirement_status;
};

static int check_pid(pid_t pid, struct code_statuses *statuses) {
    CFNumberRef pid_value = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &pid);
    if (pid_value == NULL) {
        return 2;
    }
    const void *keys[] = {kSecGuestAttributePid};
    const void *values[] = {pid_value};
    CFDictionaryRef attributes = CFDictionaryCreate(
        kCFAllocatorDefault,
        keys,
        values,
        1,
        &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks
    );
    CFRelease(pid_value);
    if (attributes == NULL) {
        return 2;
    }

    SecCodeRef guest = NULL;
    statuses->guest_status = SecCodeCopyGuestWithAttributes(NULL, attributes, kSecCSDefaultFlags, &guest);
    CFRelease(attributes);
    statuses->dynamic_status = statuses->guest_status;
    statuses->requirement_status = statuses->guest_status;
    if (statuses->guest_status == errSecSuccess) {
        statuses->dynamic_status = SecCodeCheckValidity(guest, kSecCSDefaultFlags, NULL);
        CFStringRef requirement_text = CFStringCreateWithCString(
            kCFAllocatorDefault, "anchor apple generic", kCFStringEncodingUTF8
        );
        SecRequirementRef requirement = NULL;
        if (requirement_text == NULL) {
            CFRelease(guest);
            return 2;
        }
        OSStatus requirement_create_status = SecRequirementCreateWithString(
            requirement_text, kSecCSDefaultFlags, &requirement
        );
        CFRelease(requirement_text);
        statuses->requirement_status = requirement_create_status;
        if (requirement_create_status == errSecSuccess) {
            statuses->requirement_status = SecCodeCheckValidity(guest, kSecCSDefaultFlags, requirement);
            CFRelease(requirement);
        }
        CFRelease(guest);
    }
    return 0;
}

static void print_statuses(const struct code_statuses *statuses) {
    printf(
        "{\"guest_status\":%d,\"dynamic_validity_status\":%d,\"apple_requirement_status\":%d}\n",
        (int)statuses->guest_status,
        (int)statuses->dynamic_status,
        (int)statuses->requirement_status
    );
}

static int write_marker(const char *path) {
    FILE *marker = fopen(path, "w");
    if (marker == NULL) {
        return 2;
    }
    fputs("child-user-space-ran\n", marker);
    fclose(marker);
    sleep(10);
    return 0;
}

static int spawn_suspended_and_reject(const char *self, const char *marker_path) {
    posix_spawnattr_t attributes;
    if (posix_spawnattr_init(&attributes) != 0) {
        return 2;
    }
    short flags = POSIX_SPAWN_START_SUSPENDED;
    if (posix_spawnattr_setflags(&attributes, flags) != 0) {
        posix_spawnattr_destroy(&attributes);
        return 2;
    }
    char *child_argv[] = {(char *)self, "--write-marker", (char *)marker_path, NULL};
    pid_t child_pid = 0;
    int spawn_status = posix_spawn(&child_pid, self, NULL, &attributes, child_argv, environ);
    posix_spawnattr_destroy(&attributes);
    if (spawn_status != 0) {
        return 2;
    }

    struct stat marker_status;
    int marker_absent_before_resume = lstat(marker_path, &marker_status) != 0;
    struct code_statuses statuses;
    if (check_pid(child_pid, &statuses) != 0) {
        kill(child_pid, SIGKILL);
        waitpid(child_pid, NULL, 0);
        return 2;
    }
    int killed = kill(child_pid, SIGKILL) == 0;
    waitpid(child_pid, NULL, 0);
    int marker_absent_after_reap = lstat(marker_path, &marker_status) != 0;
    printf(
        "{\"marker_absent_before_resume\":%s,\"marker_absent_after_reap\":%s,"
        "\"child_killed_without_resume\":%s,\"guest_status\":%d,"
        "\"dynamic_validity_status\":%d,\"apple_requirement_status\":%d}\n",
        marker_absent_before_resume ? "true" : "false",
        marker_absent_after_reap ? "true" : "false",
        killed ? "true" : "false",
        (int)statuses.guest_status,
        (int)statuses.dynamic_status,
        (int)statuses.requirement_status
    );
    return 0;
}

int main(int argc, char *argv[]) {
    if (argc == 2 && strcmp(argv[1], "--hold") == 0) {
        sleep(10);
        return 0;
    }
    if (argc == 3 && strcmp(argv[1], "--write-marker") == 0) {
        return write_marker(argv[2]);
    }
    if (argc == 3 && strcmp(argv[1], "--spawn-suspended-and-reject") == 0) {
        return spawn_suspended_and_reject(argv[0], argv[2]);
    }
    if (argc != 2) {
        fprintf(stderr, "usage: %s PID | --hold\n", argv[0]);
        return 2;
    }
    char *end = NULL;
    long value = strtol(argv[1], &end, 10);
    if (end == argv[1] || *end != '\0' || value <= 0) {
        fprintf(stderr, "invalid PID\n");
        return 2;
    }
    struct code_statuses statuses;
    if (check_pid((pid_t)value, &statuses) != 0) {
        return 2;
    }
    print_statuses(&statuses);
    return 0;
}
