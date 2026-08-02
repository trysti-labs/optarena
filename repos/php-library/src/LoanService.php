<?php

namespace App;

require_once __DIR__ . '/BookRepository.php';
require_once __DIR__ . '/MemberRepository.php';
require_once __DIR__ . '/LoanRepository.php';
require_once __DIR__ . '/LibraryException.php';

class LoanService
{
    private BookRepository $books;
    private MemberRepository $members;
    private LoanRepository $loans;

    public function __construct(BookRepository $books, MemberRepository $members, LoanRepository $loans)
    {
        $this->books = $books;
        $this->members = $members;
        $this->loans = $loans;
    }

    public function borrow(int $bookId, int $memberId): Loan
    {
        $book = $this->books->find($bookId);
        if ($book === null) {
            throw new LibraryException('book not found');
        }
        $member = $this->members->find($memberId);
        if ($member === null) {
            throw new LibraryException('member not found');
        }
        if ($book->availableCopies <= 0) {
            throw new LibraryException('no copies available');
        }

        $book->availableCopies--;
        return $this->loans->create($bookId, $memberId, 'active');
    }

    public function returnLoan(int $loanId): Loan
    {
        $loan = $this->loans->find($loanId);
        if ($loan === null) {
            throw new LibraryException('loan not found');
        }
        if ($loan->status !== 'active') {
            throw new LibraryException('loan already returned');
        }

        $loan->status = 'returned';
        $book = $this->books->find($loan->bookId);
        if ($book !== null) {
            $book->availableCopies++;
        }
        return $loan;
    }
}
